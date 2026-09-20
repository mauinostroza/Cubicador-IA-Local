import json
import math
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from cubicador.ocr_algorithms import (
    DetectionConfig, OcrAlgorithmError, RecognitionConfig, crop_box,
    ctc_decode, db_postprocess, dictionary_from_config,
    load_embedded_dictionary, order_boxes, preprocess_detection,
    preprocess_recognition,
)


class OcrAlgorithmTests(unittest.TestCase):
    class FakeGeometry:
        def __init__(self, boxes=()): self.boxes = boxes; self.params = None
        def db_boxes(self, probability, **kwargs): self.params = kwargs; return self.boxes
        def perspective_crop(self, image, box):
            left, top = int(box[0][0]), int(box[0][1])
            right, bottom = int(box[2][0]), int(box[2][1])
            return image.crop((left, top, right, bottom))

    def test_detection_preprocess_bgr_resize_and_normalization(self):
        image = Image.new("RGB", (100, 50), (255, 128, 0))
        tensor, size = preprocess_detection(image)
        self.assertEqual(size, (1024, 512))
        self.assertEqual((len(tensor), len(tensor[0]), len(tensor[0][0])), (3, 512, 1024))
        self.assertAlmostEqual(tensor[0][20][20], (0/255-.485)/.229, places=6)
        self.assertAlmostEqual(tensor[1][20][20], (128/255-.456)/.224, places=6)
        self.assertAlmostEqual(tensor[2][20][20], (1-.406)/.225, places=6)

    def test_detection_long_side_is_bounded(self):
        tensor, size = preprocess_detection(Image.new("RGB", (2000, 1000)))
        self.assertEqual(size, (1024, 512))
        self.assertEqual(len(tensor[0][0]), 1024)

    def test_db_threshold_score_unclip_scale_and_order(self):
        probability = [[0.0] * 10 for _ in range(8)]
        lower = ((10.,50.),(30.,50.),(30.,70.),(10.,70.))
        upper = ((60.,10.),(90.,10.),(90.,30.),(60.,30.))
        geometry = self.FakeGeometry((lower, upper))
        boxes = db_postprocess(probability, (100, 80), geometry=geometry)
        self.assertEqual(len(boxes), 2)
        self.assertEqual(boxes, (upper, lower))
        self.assertEqual(geometry.params, {"threshold": .3, "box_threshold": .6,
                                          "max_candidates": 1000, "unclip_ratio": 1.5})
        self.assertTrue(all(0 <= x <= 100 and 0 <= y <= 80 for box in boxes for x, y in box))

    def test_db_rejects_low_score_and_corrupt_maps(self):
        low = [[.0] * 3 for _ in range(3)]
        with self.assertRaises(OcrAlgorithmError): db_postprocess(low, (3, 3))
        self.assertEqual(db_postprocess(low, (3, 3), geometry=self.FakeGeometry()), ())
        for invalid in ([[0.2], [0.2, 0.3]], [[math.nan]]):
            with self.subTest(invalid=invalid), self.assertRaises(OcrAlgorithmError):
                db_postprocess(invalid, (10, 10), geometry=self.FakeGeometry())

    def test_candidate_quota_is_enforced(self):
        probability = [[0.0] * 12 for _ in range(12)]
        box = ((0.,0.),(1.,0.),(1.,1.),(0.,1.))
        with self.assertRaises(OcrAlgorithmError):
            db_postprocess(probability, (12, 12), DetectionConfig(max_candidates=1), self.FakeGeometry((box, box)))

    def test_crop_and_recognition_shape_padding(self):
        image = Image.new("RGB", (100, 40), (255, 255, 255))
        crop = crop_box(image, ((10, 5), (90, 5), (90, 35), (10, 35)), geometry=self.FakeGeometry())
        self.assertEqual(crop.size, (80, 30))
        tensor = preprocess_recognition(crop)
        self.assertEqual((len(tensor), len(tensor[0]), len(tensor[0][0])), (3, 48, 320))
        self.assertEqual(tensor[0][0][0], 1.0)
        self.assertEqual(tensor[0][0][200], 0.0)  # padding tensor cero

    def test_geometry_and_pixel_bombs_are_rejected(self):
        image = Image.new("RGB", (20, 20))
        with self.assertRaises(OcrAlgorithmError):
            crop_box(image, ((1, 1), (2, 2), (3, 3), (float("nan"), 4)), geometry=self.FakeGeometry())
        with self.assertRaises(OcrAlgorithmError):
            crop_box(image, ((1,1),(2,1),(2,2),(1,2)))
        with self.assertRaises(OcrAlgorithmError):
            preprocess_detection(image, DetectionConfig(max_source_pixels=100))

    def test_embedded_dictionary_and_external_path_keys_are_rejected(self):
        valid = {"Global": {"use_gpu": False},
                 "PostProcess": {"name": "CTCLabelDecode", "character_dict": ["a", "ñ", "31"],
                                 "use_space_char": True}}
        self.assertEqual(dictionary_from_config(valid), ("a", "ñ", "31"))
        for invalid in ({"dictionary_path": "x"},
                        {"PostProcess": {"name": "Other", "character_dict": ["a"]}},
                        {"PostProcess": {"name": "CTCLabelDecode", "character_dict": ["a", "a"]}},
                        {"PostProcess": {"name": "CTCLabelDecode", "character_dict": []}}):
            with self.subTest(invalid=invalid), self.assertRaises(OcrAlgorithmError):
                dictionary_from_config(invalid)

    def test_dictionary_file_limits_corruption_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); good = root / "dict.json"
            good.write_text(json.dumps({"PostProcess": {"name": "CTCLabelDecode", "character_dict": ["A", "B"]}}), "utf-8")
            self.assertEqual(load_embedded_dictionary(good), ("A", "B"))
            bad = root / "bad.json"; bad.write_text("{", "utf-8")
            with self.assertRaises(OcrAlgorithmError): load_embedded_dictionary(bad)
            link = root / "link.json"; link.symlink_to(good)
            with self.assertRaises(OcrAlgorithmError): load_embedded_dictionary(link)

    def test_ctc_greedy_decode_collapses_repeats_and_blank(self):
        # blank=0, a=1, b=2: a,a,blank,a,b -> aab
        logits = ((0, .95, .05), (0, .9, .1), (.99, .01, 0), (0, .8, .2), (0, .1, .9))
        text, confidence = ctc_decode(logits, ("a", "b"))
        self.assertEqual(text, "aab")
        self.assertAlmostEqual(confidence, (.95 + .8 + .9) / 3)

    def test_ctc_rejects_nonfinite_wrong_shape_and_quota(self):
        for logits in (((0, 1),), ((0, math.inf, 1),), ((0, 2, 0),)):
            with self.subTest(logits=logits), self.assertRaises(OcrAlgorithmError):
                ctc_decode(logits, ("a", "b"))
        with self.assertRaises(OcrAlgorithmError):
            ctc_decode(((0, 1, 0),) * 2, ("a", "b"), max_steps=1)

    def test_official_line_tolerance_orders_left_to_right(self):
        right = ((50., 10.), (60., 10.), (60., 20.), (50., 20.))
        left = ((10., 15.), (20., 15.), (20., 25.), (10., 25.))
        self.assertEqual(order_boxes((right, left)), (left, right))

    def test_official_descending_loop_reorders_three_boxes(self):
        right = ((70.,10.),(80.,10.),(80.,20.),(70.,20.))
        middle = ((40.,14.),(50.,14.),(50.,24.),(40.,24.))
        left = ((10.,18.),(20.,18.),(20.,28.),(10.,28.))
        self.assertEqual(order_boxes((right, middle, left)), (left, middle, right))

    def test_quad_contract_rejects_wrong_order_intersection_and_bounds(self):
        valid = ((1.,1.),(9.,1.),(9.,9.),(1.,9.))
        wrong = (valid[0], valid[3], valid[2], valid[1])
        crossed = (valid[0], valid[2], valid[1], valid[3])
        probability = [[0.0] * 2 for _ in range(2)]
        for box in (wrong, crossed, ((-1.,1.),(9.,1.),(9.,9.),(1.,9.))):
            with self.subTest(box=box), self.assertRaises(OcrAlgorithmError):
                db_postprocess(probability, (10,10), geometry=self.FakeGeometry((box,)))

    def test_ctc_requires_blank_zero_and_normalized_nonzero_rows(self):
        with self.assertRaises(OcrAlgorithmError):
            ctc_decode(((0.,1.),), ("a",), blank=1)
        for row in ((0., 0.), (.2, .2), (.6, .4001)):
            with self.subTest(row=row), self.assertRaises(OcrAlgorithmError):
                ctc_decode((row,), ("a",))

    def test_source_tensor_and_map_have_independent_budgets(self):
        with self.assertRaises(OcrAlgorithmError):
            DetectionConfig(max_source_pixels=16_000_001)
        with self.assertRaises(OcrAlgorithmError):
            DetectionConfig(max_tensor_pixels=1_500_001)
        probability = [[0.0] * 2 for _ in range(2)]
        with self.assertRaises(OcrAlgorithmError):
            db_postprocess(probability, (2000, 1000),
                           DetectionConfig(max_source_pixels=100), geometry=self.FakeGeometry())

    def test_configs_are_strict(self):
        with self.assertRaises(OcrAlgorithmError): DetectionConfig(resize_long=950)
        with self.assertRaises(OcrAlgorithmError): DetectionConfig(max_candidates=1001)
        with self.assertRaises(OcrAlgorithmError): RecognitionConfig(width=321)


if __name__ == "__main__":
    unittest.main()
