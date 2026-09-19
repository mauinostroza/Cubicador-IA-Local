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
class VerifiedToolchain:
    """Resultado de una única verificación completa del bundle.

    ``files`` es el inventario ya atestado.  Las resoluciones de cuatro
    herramientas pueden reutilizar este resultado sin volver a leer/hashar el
    mismo bundle; la atestación de lanzamiento vuelve a crear uno nuevo.
    """
    root: Path
    files: frozenset[str]
    components: dict[str, frozenset[str]]
    tools: dict[str, str]
    versions: dict[str, str]


@dataclass(frozen=True, slots=True)
class TrustedToolchain:
    vendor_root: Path
    trusted_manifest_sha256: str = TRUSTED_TOOLCHAIN_MANIFEST_SHA256

    def verify_bundle(self) -> VerifiedToolchain:
        """Verifica el manifiesto, su inventario y todos los cierres una vez."""
        if _reparse(self.vendor_root):
            raise ToolchainError("La raíz del toolchain no puede ser un enlace/reparse point")
        root = self.vendor_root.resolve(strict=True)
        manifest = root / "toolchain-manifest.json"
        pin_sidecar = root / "toolchain-manifest.sha256"
        if self.trusted_manifest_sha256 == "0"*64 or not re.fullmatch(r"[0-9a-f]{64}", self.trusted_manifest_sha256):
            raise ToolchainError("Toolchain release no fijado en el código")
        if _reparse(manifest) or _sha(manifest) != self.trusted_manifest_sha256:
            raise ToolchainError("Manifest toolchain no confiable")
        if pin_sidecar.exists():
            if _reparse(pin_sidecar) or pin_sidecar.read_text("ascii").strip() != self.trusted_manifest_sha256:
                raise ToolchainError("Pin sidecar no coincide con manifest")
        try:
            spec = json.loads(manifest.read_text("utf-8"))
            files = spec["files"]; tools = spec["tools"]
            schema = spec.get("schema_version")
            if (schema not in (1, 2) or not isinstance(files, dict)
                    or not files or not isinstance(tools, dict) or not tools):
                raise ValueError("manifest incompleto")
            actual: set[str] = set()
            for candidate in root.rglob("*"):
                if _reparse(candidate): raise ValueError("symlink/reparse no permitido")
                if candidate in (manifest, pin_sidecar) or candidate.is_dir(): continue
                actual.add(candidate.relative_to(root).as_posix())
            if actual != set(files): raise ValueError("inventario no coincide")
            normalized: dict[str, str] = {}
            component_of: dict[str, str] = {}
            for relative, expected in files.items():
                if not isinstance(relative, str) or not isinstance(expected, (str, dict)):
                    raise ValueError("entrada de inventario inválida")
                if isinstance(expected, dict):
                    digest = expected.get("sha256"); size = expected.get("size")
                    component = expected.get("component")
                    if not isinstance(digest, str) or not isinstance(size, int) or size < 0 or not isinstance(component, str):
                        raise ValueError("metadatos de inventario inválidos")
                else:
                    digest, size, component = expected, None, None
                normalized[relative] = digest
                if component is not None: component_of[relative] = component
                path = ensure_within(root / relative, root)
                if (Path(relative).is_absolute() or _reparse(path)
                        or not re.fullmatch(r"[0-9a-f]{64}", digest)
                        or not path.is_file() or (size is not None and path.stat().st_size != size)
                        or _sha(path) != digest):
                    raise ValueError("hash alterado")
            components: dict[str, frozenset[str]] = {}
            versions: dict[str, str] = {}
            if schema == 2:
                raw_components = spec.get("components")
                limits = spec.get("limits")
                release = spec.get("release")
                if not isinstance(raw_components, dict) or not isinstance(limits, dict):
                    raise ValueError("componentes o límites ausentes")
                if not isinstance(release, dict):
                    raise ValueError("metadata de release ausente")
                max_files = limits.get("max_files"); max_bytes = limits.get("max_bytes")
                if not isinstance(max_files, int) or not isinstance(max_bytes, int) or max_files <= 0 or max_bytes <= 0:
                    raise ValueError("límites globales inválidos")
                if len(normalized) > max_files or sum((root / key).stat().st_size for key in normalized) > max_bytes:
                    raise ValueError("bundle excede límites globales")
                for name, raw in raw_components.items():
                    if not isinstance(name, str) or not isinstance(raw, dict): raise ValueError("componente inválido")
                    members = raw.get("files"); version = raw.get("version")
                    comp_limits = raw.get("limits", {})
                    if not isinstance(members, list) or not members or not isinstance(version, str) or not version:
                        raise ValueError("cierre/version de componente inválido")
                    if not isinstance(comp_limits, dict): raise ValueError("límites de componente inválidos")
                    cmax_files = comp_limits.get("max_files"); cmax_bytes = comp_limits.get("max_bytes")
                    if not isinstance(cmax_files, int) or not isinstance(cmax_bytes, int) or cmax_files <= 0 or cmax_bytes <= 0:
                        raise ValueError("límites de componente inválidos")
                    member_set = frozenset(members)
                    if len(member_set) != len(members) or not member_set <= set(normalized):
                        raise ValueError("cierre de componente no coincide con inventario")
                    if len(member_set) > cmax_files or sum((root / key).stat().st_size for key in member_set) > cmax_bytes:
                        raise ValueError("componente excede límites")
                    if any(component_of.get(key) not in (None, name) for key in member_set):
                        raise ValueError("asignación de componente inconsistente")
                    components[name] = member_set; versions[name] = version
                for name, version in versions.items():
                    if release.get(f"{name}_version") != version:
                        raise ValueError("versión de componente no coincide con release")
                if set(component_of) != set(normalized) or set().union(*components.values()) != set(normalized):
                    raise ValueError("todo archivo debe pertenecer a un único cierre")
                if sum(len(c) for c in components.values()) != len(normalized):
                    raise ValueError("cierres solapados")
            else:
                # Compatibilidad con los manifests P0/P1; nuevos releases deben
                # usar schema 2 para obtener límites y cierres de componentes.
                components = {"legacy": frozenset(normalized)}; versions = {"legacy": "unversioned"}
            clean_tools: dict[str, str] = {}
            for logical_name, relative_target in tools.items():
                if not isinstance(logical_name, str) or not isinstance(relative_target, str): raise ValueError("herramienta inválida")
                target = ensure_within(root / relative_target, root)
                if (Path(relative_target).is_absolute() or relative_target not in normalized
                        or _reparse(target) or not target.is_file()):
                    raise ValueError("herramienta fuera del inventario")
                clean_tools[logical_name] = relative_target
            return VerifiedToolchain(root, frozenset(normalized), components, clean_tools, versions)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, SecurityViolation) as exc:
            raise ToolchainError(f"Toolchain no verificable: {exc}") from exc

    def verify_and_resolve(self, logical_name: str) -> Path:
        bundle = self.verify_bundle()
        try:
            return ensure_within(bundle.root / bundle.tools[logical_name], bundle.root)
        except (KeyError, SecurityViolation) as exc:
            raise ToolchainError(f"Herramienta no autorizada: {logical_name}") from exc

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
