"""Algoritmos mínimos PP-OCRv5, puros y sin activación de inferencia.

Este módulo transforma entradas/salidas numéricas. No importa Paddle, no abre red
y no conoce rutas de modelos. El proceso aislado podrá inyectar un ``Predictor``
cuando el payload firmado exista.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Protocol, Sequence

from PIL import Image


class OcrAlgorithmError(ValueError):
    pass


Tensor3 = tuple[tuple[tuple[float, ...], ...], ...]
Point = tuple[float, float]
Quad = tuple[Point, Point, Point, Point]


class Predictor(Protocol):
    """Frontera prevista para ``paddle.inference.Predictor`` en el worker."""

    def run(self, inputs: dict[str, Tensor3]) -> dict[str, object]: ...


class GeometryBackend(Protocol):
    """Geometría DB/perspectiva fijada (futuro OpenCV + pyclipper)."""

    def db_boxes(self, probability: Sequence[Sequence[float]], *, threshold: float,
                 box_threshold: float, max_candidates: int,
                 unclip_ratio: float) -> Sequence[Quad]: ...

    def perspective_crop(self, image: Image.Image, box: Quad) -> Image.Image: ...


@dataclass(frozen=True, slots=True)
class DetectionConfig:
    resize_long: int = 960
    threshold: float = .3
    box_threshold: float = .6
    max_candidates: int = 1000
    unclip_ratio: float = 1.5
    max_source_pixels: int = 16_000_000
    max_tensor_pixels: int = 1_500_000

    def __post_init__(self) -> None:
        if self.resize_long != 960:
            raise OcrAlgorithmError("PP-OCRv5 det requiere resize_long=960")
        if not 0 < self.threshold < 1 or not 0 < self.box_threshold <= 1:
            raise OcrAlgorithmError("umbrales DB inválidos")
        if not 1 <= self.max_candidates <= 1000 or not 1 <= self.unclip_ratio <= 4:
            raise OcrAlgorithmError("límites DB inválidos")
        if not 1 <= self.max_source_pixels <= 16_000_000:
            raise OcrAlgorithmError("cuota de imagen fuente inválida")
        if not 1 <= self.max_tensor_pixels <= 1_500_000:
            raise OcrAlgorithmError("cuota de tensor/mapa inválida")


@dataclass(frozen=True, slots=True)
class RecognitionConfig:
    height: int = 48
    width: int = 320
    max_dictionary_chars: int = 20_000

    def __post_init__(self) -> None:
        if (self.height, self.width) != (48, 320):
            raise OcrAlgorithmError("PP-OCRv5 rec requiere 3x48x320")


def _safe_image(image: Image.Image, max_pixels: int) -> Image.Image:
    if image.width <= 0 or image.height <= 0 or image.width * image.height > max_pixels:
        raise OcrAlgorithmError("imagen OCR fuera de cuota")
    image.load()
    return image.convert("RGB")


def preprocess_detection(image: Image.Image, config: DetectionConfig = DetectionConfig()) -> tuple[Tensor3, tuple[int, int]]:
    """BGR, resize long=960, múltiplos de 32 y normalización PP-OCR."""
    source = _safe_image(image, config.max_source_pixels)
    ratio = config.resize_long / max(source.size)
    width = max(128, math.ceil(source.width * ratio / 128) * 128)
    height = max(128, math.ceil(source.height * ratio / 128) * 128)
    if width * height > config.max_tensor_pixels:
        raise OcrAlgorithmError("tensor de detección fuera de cuota")
    resized = source.resize((width, height), Image.Resampling.BILINEAR)
    pixels = resized.load()
    # Configuración PP-OCRv5 det: BGR / 255, ImageNet mean/std, HWC -> CHW.
    channels = [[[0.0 for _ in range(width)] for _ in range(height)] for _ in range(3)]
    means = (.485, .456, .406); stds = (.229, .224, .225)
    for y in range(height):
        for x in range(width):
            r, g, b = pixels[x, y]
            for channel, value in enumerate((b, g, r)):
                channels[channel][y][x] = (value / 255.0 - means[channel]) / stds[channel]
    return tuple(tuple(tuple(row) for row in channel) for channel in channels), (width, height)


def _validate_probability_map(probability: Sequence[Sequence[float]], max_pixels: int) -> tuple[int, int]:
    height = len(probability)
    width = len(probability[0]) if height else 0
    if not height or not width or width * height > max_pixels:
        raise OcrAlgorithmError("mapa DB vacío o fuera de cuota")
    for row in probability:
        if len(row) != width:
            raise OcrAlgorithmError("mapa DB irregular")
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1 for value in row):
            raise OcrAlgorithmError("probabilidad DB inválida")
    return width, height


def db_postprocess(probability: Sequence[Sequence[float]], image_size: tuple[int, int],
                   config: DetectionConfig = DetectionConfig(),
                   geometry: GeometryBackend | None = None) -> tuple[Quad, ...]:
    """Valida cuotas y delega DB real; sin geometría fijada falla cerrado."""
    width, height = _validate_probability_map(probability, config.max_tensor_pixels)
    image_width, image_height = image_size
    if image_width <= 0 or image_height <= 0 or image_width * image_height > config.max_source_pixels:
        raise OcrAlgorithmError("geometría de destino inválida")
    if geometry is None:
        raise OcrAlgorithmError("geometría DB no activada: falta backend fijado")
    boxes = tuple(geometry.db_boxes(probability, threshold=config.threshold,
                                    box_threshold=config.box_threshold,
                                    max_candidates=config.max_candidates,
                                    unclip_ratio=config.unclip_ratio))
    if len(boxes) > config.max_candidates:
        raise OcrAlgorithmError("backend DB excedió la cuota")
    # El backend retorna geometría ya escalada a la imagen original.
    for box in boxes:
        _validate_quad(box, image_width, image_height)
    return order_boxes(boxes)


def order_boxes(boxes: Sequence[Quad]) -> tuple[Quad, ...]:
    """Orden PaddleOCR: y/x y corrección adyacente con tolerancia de 10 px."""
    def key(box: Quad) -> tuple[float, float]:
        _validate_quad(box)
        return min(p[1] for p in box), min(p[0] for p in box)
    ordered = list(sorted(boxes, key=key))
    for i in range(len(ordered) - 1):
        for j in range(i, -1, -1):
            y0, x0 = key(ordered[j]); y1, x1 = key(ordered[j + 1])
            if abs(y1 - y0) < 10 and x1 < x0:
                ordered[j], ordered[j + 1] = ordered[j + 1], ordered[j]
            else:
                break
    return tuple(ordered)


def _validate_quad(box: Quad, width: int | None = None, height: int | None = None) -> None:
    if len(box) != 4 or any(len(point) != 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in point) for point in box):
        raise OcrAlgorithmError("cuadrilátero inválido")
    if width is not None and height is not None and any(not (0 <= x <= width and 0 <= y <= height) for x, y in box):
        raise OcrAlgorithmError("cuadrilátero fuera de imagen")
    tl, tr, br, bl = box
    if not ((tl[1] + tr[1]) / 2 < (bl[1] + br[1]) / 2
            and (tl[0] + bl[0]) / 2 < (tr[0] + br[0]) / 2):
        raise OcrAlgorithmError("cuadrilátero no respeta TL/TR/BR/BL")
    crosses = []
    for index in range(4):
        a, b, c = box[index], box[(index + 1) % 4], box[(index + 2) % 4]
        crosses.append((b[0]-a[0])*(c[1]-b[1]) - (b[1]-a[1])*(c[0]-b[0]))
        if math.hypot(b[0]-a[0], b[1]-a[1]) <= 0:
            raise OcrAlgorithmError("lado de cuadrilátero inválido")
    if not all(value > 0 for value in crosses):
        raise OcrAlgorithmError("cuadrilátero degenerado o autointersectado")


def crop_box(image: Image.Image, box: Quad, *, geometry: GeometryBackend | None = None,
             max_pixels: int = 5_000_000) -> Image.Image:
    source = _safe_image(image, max_pixels=16_000_000)
    _validate_quad(box, source.width, source.height)
    if geometry is None:
        raise OcrAlgorithmError("recorte perspectiva no activado: falta backend fijado")
    cropped = geometry.perspective_crop(source, box)
    if not isinstance(cropped, Image.Image) or cropped.width <= 0 or cropped.height <= 0 or cropped.width * cropped.height > max_pixels:
        raise OcrAlgorithmError("recorte OCR inválido o fuera de cuota")
    return cropped


def preprocess_recognition(image: Image.Image, config: RecognitionConfig = RecognitionConfig()) -> Tensor3:
    source = _safe_image(image, config.height * config.width * 100)
    resized_width = min(config.width, max(1, math.ceil(config.height * source.width / source.height)))
    resized = source.resize((resized_width, config.height), Image.Resampling.BILINEAR)
    pixels = resized.load(); channels = [[[0.0 for _ in range(config.width)] for _ in range(config.height)] for _ in range(3)]
    for y in range(config.height):
        for x in range(config.width):
            if x >= resized_width:
                continue
            r, g, b = pixels[x, y]
            for channel, value in enumerate((b, g, r)):
                channels[channel][y][x] = value / 127.5 - 1.0
    return tuple(tuple(tuple(row) for row in channel) for channel in channels)


def dictionary_from_config(raw: object, config: RecognitionConfig = RecognitionConfig()) -> tuple[str, ...]:
    """Carga solo el diccionario embebido; nunca sigue una ruta externa."""
    if (not isinstance(raw, dict) or "PostProcess" not in raw
            or not isinstance(raw["PostProcess"], dict)
            or raw["PostProcess"].get("name") != "CTCLabelDecode"
            or not isinstance(raw["PostProcess"].get("character_dict"), list)):
        raise OcrAlgorithmError("configuración de diccionario inválida")
    chars = raw["PostProcess"]["character_dict"]
    if not 1 <= len(chars) <= config.max_dictionary_chars or any(not isinstance(v, str) or not v or len(v) > 8 for v in chars):
        raise OcrAlgorithmError("diccionario OCR fuera de cuota")
    if len(set(chars)) != len(chars):
        raise OcrAlgorithmError("diccionario OCR contiene duplicados")
    return tuple(chars)


def load_embedded_dictionary(path: Path, *, max_bytes: int = 256_000) -> tuple[str, ...]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0 or path.stat().st_size > max_bytes:
        raise OcrAlgorithmError("archivo de configuración inválido")
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OcrAlgorithmError("configuración OCR corrupta") from exc
    return dictionary_from_config(raw)


def ctc_decode(logits: Sequence[Sequence[float]], dictionary: Sequence[str], *, blank: int = 0,
               max_steps: int = 4096) -> tuple[str, float]:
    if not dictionary or len(set(dictionary)) != len(dictionary) or blank != 0:
        raise OcrAlgorithmError("diccionario/blank CTC inválido")
    if not 1 <= len(logits) <= max_steps:
        raise OcrAlgorithmError("secuencia CTC fuera de cuota")
    classes = len(dictionary) + 1
    result: list[str] = []; confidences: list[float] = []; previous = -1
    for row in logits:
        if len(row) != classes or any(not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in row):
            raise OcrAlgorithmError("probabilidades CTC inválidas")
        total = sum(float(v) for v in row)
        if total <= 0 or not math.isclose(total, 1.0, rel_tol=1e-5, abs_tol=1e-6):
            raise OcrAlgorithmError("fila CTC no es una distribución de probabilidad")
        index = max(range(classes), key=lambda i: row[i])
        if index != blank and index != previous:
            dictionary_index = index - 1 if index > blank else index
            result.append(dictionary[dictionary_index])
            confidences.append(float(row[index]))
        previous = index
    return "".join(result), (sum(confidences) / len(confidences) if confidences else 0.0)
