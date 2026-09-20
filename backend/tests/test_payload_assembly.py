import hashlib, io, json, os, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from assemble_ocr_payload import AssemblyError, assemble
from cubicador.toolchain import TrustedToolchain

def sha(data): return hashlib.sha256(data).hexdigest()

class PayloadAssemblyTests(unittest.TestCase):
    def fixture(self, root, *, blocked=None, wheel_entries=None):
        source = root / "source"; source.mkdir()
        wheel_entries = wheel_entries or {"paddle/__init__.py": b"runtime", "paddle/base/core.pyd": b"binary"}
        wheel = source / "runtime.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            for name, data in wheel_entries.items(): archive.writestr(name, data)
        records = {
            "runner": ("runner.exe", "paddle/bin/runner.exe", "runner", "file", b"runner"),
            "runtime": ("runtime.whl", "paddle/runtime", "runtime", "wheel", wheel.read_bytes()),
            "det": ("det.json", "paddle/models/det/inference.json", "models", "file", b"det"),
            "rec": ("rec.yml", "paddle/models/rec/inference.yml", "models", "file", b"rec"),
        }
        artifacts, inputs = {}, []
        for name, (src, dest, component, kind, data) in records.items():
            path = source / src
            if not path.exists(): path.write_bytes(data)
            artifacts[name] = {"status": "blocked_no_pin" if name == blocked else "verified_release_input",
                               "version": "fixture-1", "sha256": sha(data), "size_bytes": len(data)}
            inputs.append({"artifact": name, "component": component, "source": src, "destination": dest, "kind": kind})
        lock = root / "lock.json"
        lock.write_text(json.dumps({"release_enabled": False, "feature_gates": {"pdfium": False, "paddle_ocr": False},
            "artifacts": artifacts, "payload_assembly": {"schema_version": 2, "offline_only": True, "inputs": inputs}}))
        return lock, source

    def test_wheel_is_extracted_and_every_runtime_file_is_inventoried(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw); lock,source=self.fixture(root); output=root/"payload"; manifest=assemble(lock,source,output)
            self.assertNotIn("paddle/runtime/runtime.whl", manifest["files"])
            self.assertIn("paddle/runtime/paddle/base/core.pyd", manifest["files"])
            self.assertFalse(any(name.endswith((".whl", ".tar")) for name in manifest["files"]))
            pin=(output/"toolchain-manifest.sha256").read_text().strip()
            self.assertTrue(TrustedToolchain(output,pin).verify_and_resolve("paddle.runner").is_file())

    def test_real_contract_stays_closed_on_runner(self):
        root=Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as raw:
            output=Path(raw)/"payload"
            with self.assertRaisesRegex(AssemblyError,"paddleocr_runner"): assemble(root/"ocr-release.lock.json",Path(raw),output)
            self.assertFalse(output.exists())

    def test_wheel_traversal_duplicate_symlink_and_ratio_are_rejected(self):
        cases=[]
        cases.append({"../escape": b"x"})
        cases.append({"A.txt": b"x", "a.TXT": b"y"})
        for entries in cases:
            with self.subTest(entries=list(entries)), tempfile.TemporaryDirectory() as raw:
                root=Path(raw); lock,source=self.fixture(root,wheel_entries=entries)
                with self.assertRaises(AssemblyError): assemble(lock,source,root/"out")
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw); lock,source=self.fixture(root); wheel=source/"runtime.whl"
            info=zipfile.ZipInfo("link"); info.external_attr=(0o120777 << 16)
            with zipfile.ZipFile(wheel,"w") as archive: archive.writestr(info,b"target")
            spec=json.loads(lock.read_text()); data=wheel.read_bytes(); spec["artifacts"]["runtime"].update(sha256=sha(data),size_bytes=len(data)); lock.write_text(json.dumps(spec))
            with self.assertRaises(AssemblyError): assemble(lock,source,root/"out")
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw); lock,source=self.fixture(root); wheel=source/"runtime.whl"
            with zipfile.ZipFile(wheel,"w",compression=zipfile.ZIP_DEFLATED) as archive: archive.writestr("bomb",b"A"*10000)
            spec=json.loads(lock.read_text()); data=wheel.read_bytes(); spec["artifacts"]["runtime"].update(sha256=sha(data),size_bytes=len(data)); lock.write_text(json.dumps(spec))
            with patch("assemble_ocr_payload.MAX_RATIO",2), self.assertRaises(AssemblyError): assemble(lock,source,root/"out")

    def test_hardlink_symlink_tamper_and_destination_race_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw); lock,source=self.fixture(root); os.link(source/"runner.exe",source/"runner-link.exe")
            with self.assertRaisesRegex(AssemblyError,"enlazada"): assemble(lock,source,root/"out")
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw); lock,source=self.fixture(root); (source/"det.json").write_bytes(b"tamper")
            with self.assertRaisesRegex(AssemblyError,"pin no coincide"): assemble(lock,source,root/"out")
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw); lock,source=self.fixture(root); output=root/"out"
            def race(src,dst): output.mkdir(); raise FileExistsError()
            with patch("assemble_ocr_payload._rename_no_replace",side_effect=race), self.assertRaises(FileExistsError): assemble(lock,source,output)
            self.assertEqual(list(output.iterdir()),[])
        if hasattr(os, "symlink"):
            with tempfile.TemporaryDirectory() as raw:
                root=Path(raw); lock,source=self.fixture(root); target=root/"elsewhere"; target.mkdir()
                (source/"det.json").unlink(); (source/"det.json").symlink_to(target/"missing")
                with self.assertRaises(AssemblyError): assemble(lock,source,root/"out")

    def test_strict_lock_offline_and_no_dictionary_component(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw); lock,source=self.fixture(root); text=lock.read_text().replace('"offline_only": true','"offline_only": false')
            lock.write_text(text)
            with self.assertRaises(AssemblyError): assemble(lock,source,root/"out")
            lock.write_text('{"release_enabled":false,"release_enabled":false}')
            with self.assertRaisesRegex(AssemblyError,"duplicada"): assemble(lock,source,root/"out")

    def test_windows_nonportable_source_destination_and_wheel_paths_are_rejected(self):
        for bad in ("C:/evil", "a:b", "CON", "file. "):
            with self.subTest(path=bad), tempfile.TemporaryDirectory() as raw:
                root=Path(raw); lock,source=self.fixture(root); spec=json.loads(lock.read_text())
                spec["payload_assembly"]["inputs"][0]["destination"] = bad
                lock.write_text(json.dumps(spec))
                with self.assertRaisesRegex(AssemblyError,"portable|inválida"): assemble(lock,source,root/"out")
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw); lock,source=self.fixture(root,wheel_entries={"CON.txt":b"x"})
            with self.assertRaisesRegex(AssemblyError,"portable"): assemble(lock,source,root/"out")

    def test_global_extracted_budget_includes_wheel_and_direct_files(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw); entries={"paddle/large.bin":b"A"*500}
            lock,source=self.fixture(root,wheel_entries=entries); wheel=source/"runtime.whl"
            with zipfile.ZipFile(wheel,"w",compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("paddle/large.bin",b"A"*500)
            spec=json.loads(lock.read_text()); data=wheel.read_bytes()
            spec["artifacts"]["runtime"].update(sha256=sha(data),size_bytes=len(data))
            lock.write_text(json.dumps(spec)); spec=json.loads(lock.read_text())
            self.assertLess(sum(item["size_bytes"] for item in spec["artifacts"].values()), 500)
            with patch("assemble_ocr_payload.MAX_BYTES",500), self.assertRaisesRegex(AssemblyError,"cuota global"):
                assemble(lock,source,root/"out")

    def test_casefold_collision_between_wheel_and_direct_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw); lock,source=self.fixture(root,wheel_entries={"inference.JSON":b"runtime"})
            spec=json.loads(lock.read_text())
            spec["payload_assembly"]["inputs"][1]["destination"]="paddle/models/det"
            lock.write_text(json.dumps(spec))
            with self.assertRaisesRegex(AssemblyError,"colisión"): assemble(lock,source,root/"out")

if __name__=="__main__": unittest.main()
