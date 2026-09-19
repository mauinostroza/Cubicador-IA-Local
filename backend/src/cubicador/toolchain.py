from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import re
from collections.abc import Callable

from .security import SecurityViolation, ensure_within


# Release automation replaces this value. All-zero deliberately disables vendor execution.
TRUSTED_TOOLCHAIN_MANIFEST_SHA256 = "0" * 64


class ToolchainError(RuntimeError): pass


def _sha(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""): digest.update(chunk)
    return digest.hexdigest()


def _reparse(path: Path) -> bool:
    if path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)()):
        return True
    try:
        # Python exposes the native Windows attributes on stat_result.  Check
        # the generic bit too: reparse points are not limited to symlinks and
        # junctions.
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except OSError:
        return False


@dataclass(frozen=True, slots=True)
class TrustedToolchain:
    vendor_root: Path
    trusted_manifest_sha256: str = TRUSTED_TOOLCHAIN_MANIFEST_SHA256

    def verify_and_resolve(self, logical_name: str) -> Path:
        if _reparse(self.vendor_root):
            raise ToolchainError("La raíz del toolchain no puede ser un enlace/reparse point")
        root = self.vendor_root.resolve(strict=True)
        manifest = root / "toolchain-manifest.json"
        if self.trusted_manifest_sha256 == "0"*64 or not re.fullmatch(r"[0-9a-f]{64}", self.trusted_manifest_sha256):
            raise ToolchainError("Toolchain release no fijado en el código")
        if _reparse(manifest) or _sha(manifest) != self.trusted_manifest_sha256:
            raise ToolchainError("Manifest toolchain no confiable")
        try:
            spec = json.loads(manifest.read_text("utf-8"))
            files = spec["files"]; tools = spec["tools"]
            if (spec.get("schema_version") != 1 or not isinstance(files, dict)
                    or not files or not isinstance(tools, dict) or not tools):
                raise ValueError("manifest incompleto")
            actual: set[str] = set()
            for candidate in root.rglob("*"):
                if _reparse(candidate): raise ValueError("symlink/reparse no permitido")
                if candidate == manifest or candidate.is_dir(): continue
                actual.add(candidate.relative_to(root).as_posix())
            if actual != set(files): raise ValueError("inventario no coincide")
            for relative, expected in files.items():
                if not isinstance(relative, str) or not isinstance(expected, str):
                    raise ValueError("entrada de inventario inválida")
                path = ensure_within(root / relative, root)
                if (Path(relative).is_absolute() or _reparse(path)
                        or not re.fullmatch(r"[0-9a-f]{64}", expected)
                        or not path.is_file() or _sha(path) != expected):
                    raise ValueError("hash alterado")
            relative_target = tools[logical_name]
            if not isinstance(relative_target, str): raise ValueError("herramienta inválida")
            target = ensure_within(root / relative_target, root)
            if (Path(relative_target).is_absolute()
                    or target.relative_to(root).as_posix() not in files
                    or _reparse(target) or not target.is_file()):
                raise ValueError("herramienta fuera del inventario")
            return target
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, SecurityViolation) as exc:
            raise ToolchainError(f"Toolchain no verificable: {exc}") from exc

    def verify_for_launch(self, logical_name: str, expected: str | Path) -> Path:
        """Revalida todo el bundle and devuelve el mismo ejecutable esperado.

        Este método se usa como última comprobación desde ``run_command`` justo
        antes de crear el proceso.  Como ``verify_and_resolve`` recorre todo el
        inventario, cubre el ejecutable, DLL/runtime y modelos empaquetados.
        """
        expected_path = Path(expected).resolve(strict=True)
        actual = self.verify_and_resolve(logical_name)
        if actual != expected_path:
            raise ToolchainError("El ejecutable cambió entre verificaciones")
        return actual

    def launch_verifier(self, logical_name: str, expected: str | Path) -> Callable[[], Path]:
        """Construye una atestación tardía para ``run_command``."""
        return lambda: self.verify_for_launch(logical_name, expected)


def resolve_poppler(name: str, *, developer_mode: bool = False) -> Path:
    vendor = Path(__file__).resolve().parents[3] / "vendor"
    try:
        return TrustedToolchain(vendor).verify_and_resolve(f"poppler.{name}")
    except (ToolchainError, FileNotFoundError):
        if not developer_mode: raise
        candidate = Path("/usr/bin") / name
        if os.name != "nt" and candidate.is_file(): return candidate.resolve()
        raise ToolchainError(f"Herramienta dev no disponible: {name}")


def resolve_poppler_for_launch(name: str, *, developer_mode: bool = False) -> tuple[Path, Callable[[], Path]]:
    """Resolve Poppler and return a verifier for the final process spawn.

    Release mode can only use the pinned vendor bundle.  Developer mode may
    explicitly use ``/usr/bin`` for local tests, but even there the exact file
    hash is rechecked immediately before launch.
    """
    vendor = Path(__file__).resolve().parents[3] / "vendor"
    try:
        chain = TrustedToolchain(vendor)
        executable = chain.verify_and_resolve(f"poppler.{name}")
        return executable, chain.launch_verifier(f"poppler.{name}", executable)
    except (ToolchainError, FileNotFoundError):
        if not developer_mode:
            raise
        candidate = Path("/usr/bin") / name
        if os.name == "nt" or not candidate.is_file():
            raise ToolchainError(f"Herramienta dev no disponible: {name}")
        candidate = candidate.resolve(strict=True)
        expected_hash = _sha(candidate)

        def verify_dev() -> Path:
            if (not candidate.is_file() or _reparse(candidate)
                    or _sha(candidate) != expected_hash):
                raise ToolchainError(f"Herramienta dev alterada: {candidate}")
            return candidate.resolve(strict=True)

        return candidate, verify_dev
