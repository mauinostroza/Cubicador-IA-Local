import json, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from cubicador.toolchain import ToolchainError, TrustedToolchain, _sha, resolve_poppler_for_launch
from cubicador.runtime import run_command

class ToolchainTests(unittest.TestCase):
    def bundle(self, root):
        paths = {"poppler.pdftotext":"poppler/bin/pdftotext.exe", "poppler.pdfinfo":"poppler/bin/pdfinfo.exe",
                 "poppler.pdftoppm":"poppler/bin/pdftoppm.exe", "paddle.runner":"paddle/bin/paddle_ocr_runner.exe"}
        for rel in list(paths.values())+["paddle/models/det.bin", "paddle/runtime/core.dll"]:
            p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(rel.encode())
        files={p.relative_to(root).as_posix():_sha(p) for p in root.rglob("*") if p.is_file()}
        manifest=root/"toolchain-manifest.json"; manifest.write_text(json.dumps({"schema_version":1,"tools":paths,"files":files},sort_keys=True))
        return TrustedToolchain(root,_sha(manifest)), manifest

    def test_exact_inventory_and_external_pin(self):
        with tempfile.TemporaryDirectory() as d:
            chain,manifest=self.bundle(Path(d)); self.assertTrue(chain.verify_and_resolve("paddle.runner").is_file())
            (Path(d)/"paddle/runtime/extra.dll").write_bytes(b"x")
            with self.assertRaises(ToolchainError): chain.verify_and_resolve("paddle.runner")
            # Regenerar manifest no actualiza el pin inmutable que ya conoce el programa.
            spec=json.loads(manifest.read_text()); spec["files"]["paddle/runtime/extra.dll"]=_sha(Path(d)/"paddle/runtime/extra.dll"); manifest.write_text(json.dumps(spec,sort_keys=True))
            with self.assertRaises(ToolchainError): chain.verify_and_resolve("paddle.runner")

    def test_tamper_immediately_before_launch_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            chain,_=self.bundle(Path(d)); tool=chain.verify_and_resolve("poppler.pdftotext"); tool.write_bytes(b"replacement")
            with self.assertRaises(ToolchainError), patch("cubicador.runtime.run_command") as run:
                chain.verify_and_resolve("poppler.pdftotext")
            run.assert_not_called()

    @unittest.skipIf(os.name == "nt", "el ejecutable de prueba es un script POSIX")
    def test_launch_attestation_blocks_executable_replacement(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            runner = root / "paddle/bin/paddle_ocr_runner.exe"
            runner.parent.mkdir(parents=True)
            runner.write_text("#!/bin/sh\nexit 0\n")
            runner.chmod(0o700)
            (root / "paddle/models/det.bin").parent.mkdir(parents=True)
            (root / "paddle/models/det.bin").write_bytes(b"model")
            (root / "paddle/runtime/core.dll").parent.mkdir(parents=True)
            (root / "paddle/runtime/core.dll").write_bytes(b"dll")
            files = {p.relative_to(root).as_posix(): _sha(p) for p in root.rglob("*") if p.is_file()}
            manifest = root / "toolchain-manifest.json"
            manifest.write_text(json.dumps({"schema_version": 1,
                "tools": {"paddle.runner": "paddle/bin/paddle_ocr_runner.exe"},
                "files": files}, sort_keys=True))
            chain = TrustedToolchain(root, _sha(manifest))
            expected = chain.verify_and_resolve("paddle.runner")
            verifier = chain.launch_verifier("paddle.runner", expected)
            # La sustitución ocurre después de una primera verificación, pero
            # antes de run_command: la atestación tardía debe impedir el spawn.
            expected.write_text("#!/bin/sh\nexit 99\n")
            with self.assertRaises(ToolchainError), patch("cubicador.runtime.subprocess.Popen") as popen:
                run_command([str(expected)], workspace=root, launch_verifier=verifier)
            popen.assert_not_called()

    def test_launch_attestation_covers_model_and_dll_tampering(self):
        with tempfile.TemporaryDirectory() as d:
            chain, _ = self.bundle(Path(d))
            runner = chain.verify_and_resolve("paddle.runner")
            verifier = chain.launch_verifier("paddle.runner", runner)
            (Path(d) / "paddle/models/det.bin").write_bytes(b"replacement")
            with self.assertRaises(ToolchainError): verifier()
            (Path(d) / "paddle/models/det.bin").write_bytes(b"paddle/models/det.bin")
            (Path(d) / "paddle/runtime/core.dll").write_bytes(b"replacement")
            with self.assertRaises(ToolchainError): verifier()

    def test_placeholder_disables_release(self):
        with tempfile.TemporaryDirectory() as d:
            chain,_=self.bundle(Path(d))
            with self.assertRaises(ToolchainError): TrustedToolchain(Path(d)).verify_and_resolve("paddle.runner")

    def test_secure_poppler_resolution_never_falls_back_to_system(self):
        with patch("cubicador.toolchain.TrustedToolchain.verify_and_resolve",
                   side_effect=ToolchainError("bundle no fijado")):
            with self.assertRaises(ToolchainError):
                resolve_poppler_for_launch("pdftotext", developer_mode=False)

    def test_schema2_closures_versions_sizes_and_pin_sidecar(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            paths = {"poppler.pdftotext": "poppler/bin/pdftotext.exe", "poppler.pdfinfo": "poppler/bin/pdfinfo.exe",
                     "poppler.pdftoppm": "poppler/bin/pdftoppm.exe", "paddle.runner": "paddle/bin/paddle_ocr_runner.exe"}
            for rel in paths.values():
                p = root / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(rel.encode())
            for rel in ("paddle/models/det.bin", "paddle/runtime/core.dll"):
                p = root / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(rel.encode())
            members = {p.relative_to(root).as_posix(): {"sha256": _sha(p), "size": p.stat().st_size,
                       "component": "poppler" if p.relative_to(root).parts[0] == "poppler" else "paddle"}
                       for p in root.rglob("*") if p.is_file()}
            components = {}
            for component in ("poppler", "paddle"):
                own = [name for name, meta in members.items() if meta["component"] == component]
                components[component] = {"version": "test-1", "files": own,
                                         "limits": {"max_files": 10, "max_bytes": 10000}}
            spec = {"schema_version": 2, "release": {"poppler_version": "test-1", "paddle_version": "test-1"},
                    "limits": {"max_files": 20, "max_bytes": 20000}, "components": components,
                    "tools": paths, "files": members}
            manifest = root / "toolchain-manifest.json"
            manifest.write_text(json.dumps(spec, sort_keys=True), encoding="utf-8")
            pin = _sha(manifest); (root / "toolchain-manifest.sha256").write_text(pin + "\n", encoding="ascii")
            bundle = TrustedToolchain(root, pin).verify_bundle()
            self.assertEqual(bundle.versions, {"paddle": "test-1", "poppler": "test-1"})
            self.assertEqual(bundle.root / bundle.tools["paddle.runner"], root / paths["paddle.runner"])
            (root / "paddle/models/det.bin").write_bytes(b"tampered")
            with self.assertRaises(ToolchainError): TrustedToolchain(root, pin).verify_bundle()

    def test_schema2_rejects_unassigned_or_overlapping_closure(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); p = root / "poppler/bin/pdftotext.exe"; p.parent.mkdir(parents=True); p.write_bytes(b"x")
            files = {"poppler/bin/pdftotext.exe": {"sha256": _sha(p), "size": 1, "component": "poppler"}}
            spec = {"schema_version": 2, "limits": {"max_files": 5, "max_bytes": 100}, "components": {
                "poppler": {"version": "x", "files": list(files), "limits": {"max_files": 5, "max_bytes": 100}},
                "paddle": {"version": "x", "files": list(files), "limits": {"max_files": 5, "max_bytes": 100}}},
                "tools": {"poppler.pdftotext": list(files)[0]}, "files": files}
            m = root / "toolchain-manifest.json"; m.write_text(json.dumps(spec)); pin = _sha(m)
            with self.assertRaises(ToolchainError): TrustedToolchain(root, pin).verify_bundle()

if __name__=="__main__": unittest.main()
