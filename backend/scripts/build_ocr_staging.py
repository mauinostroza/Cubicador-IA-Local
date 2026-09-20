"""Build a non-production OCR staging artifact in CI.

This command is intentionally separate from runtime.  It may download only
the exact inputs listed in ``ocr-release.lock.json`` and only when running in
GitHub Actions/CI.  Every response is streamed to a temporary file and is
checked against the upstream PyPI SHA-256 and byte count *before* any wheel is
opened.  Blocked assets are reported and make ``--require-complete`` fail.

The command never installs, imports or executes a downloaded package.  The
staging output is evidence for review; release gates remain closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, ProxyHandler, build_opener
import zipfile


MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024
MAX_TOTAL_DOWNLOAD_BYTES = 400 * 1024 * 1024
MAX_WHEEL_FILES = 100_000
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
ALLOWED_HOSTS = frozenset({"files.pythonhosted.org"})
SCHEMA = 1


class StagingError(RuntimeError):
    pass


class _RejectRedirect(HTTPRedirectHandler):
    """No permitir que una URL fijada cambie de origen durante staging."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise StagingError(f"redirect no permitido: {newurl}")


def _open_direct(request: Request, *, timeout: int):
    """Abre exclusivamente sin proxies de entorno y sin seguir redirects."""
    opener = build_opener(ProxyHandler({}), _RejectRedirect())
    return opener.open(request, timeout=timeout)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_ci() -> bool:
    return os.environ.get("CI", "").lower() == "true" or os.environ.get("GITHUB_ACTIONS", "").lower() == "true"


def _check_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise StagingError(f"URL no permitida: {url}")
    if parsed.hostname not in ALLOWED_HOSTS:
        raise StagingError(f"host no permitido para staging: {parsed.hostname}")


def _download_verified(artifact: dict, destination: Path) -> dict:
    url = artifact.get("url")
    expected_sha = artifact.get("sha256")
    expected_size = artifact.get("size_bytes")
    filename = artifact.get("filename")
    if not all(isinstance(value, str) for value in (url, expected_sha, filename)):
        raise StagingError("asset sin URL, nombre o SHA-256 verificable")
    if not isinstance(expected_size, int) or expected_size <= 0 or expected_size > MAX_DOWNLOAD_BYTES:
        raise StagingError("asset con tamaño no verificable o fuera de cuota")
    if len(expected_sha) != 64 or any(ch not in "0123456789abcdef" for ch in expected_sha):
        raise StagingError("SHA-256 inválido")
    _check_url(url)
    if Path(filename).name != filename or filename.lower() != Path(filename).name.lower():
        raise StagingError("nombre de asset inválido")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(prefix="download-", suffix=".part", dir=destination.parent)
    os.close(fd)
    temporary = Path(raw_temp)
    total = 0
    digest = hashlib.sha256()
    try:
        request = Request(url, headers={"User-Agent": "cubicador-ocr-staging/1"})
        with _open_direct(request, timeout=120) as response, temporary.open("wb") as output:
            if response.geturl() != url:
                raise StagingError(f"redirect no permitido para {filename}")
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) != expected_size:
                raise StagingError(f"Content-Length no coincide para {filename}")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > expected_size or total > MAX_DOWNLOAD_BYTES:
                    raise StagingError(f"descarga excede tamaño fijado para {filename}")
                digest.update(chunk)
                output.write(chunk)
        actual = digest.hexdigest()
        if total != expected_size or actual != expected_sha:
            raise StagingError(f"hash/tamaño no coincide para {filename}: {actual=} {total=}")
        os.replace(temporary, destination)
        return {"filename": filename, "url": url, "sha256": actual, "size_bytes": total}
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _safe_extract_wheel(wheel: Path, target: Path, *, remaining_bytes: int | None = None) -> list[str]:
    extracted: list[str] = []
    with zipfile.ZipFile(wheel) as archive:
        members = archive.infolist()
        if len(members) > MAX_WHEEL_FILES:
            raise StagingError("wheel excede la cuota de archivos")
        total_uncompressed = sum(member.file_size for member in members)
        quota = MAX_EXTRACTED_BYTES if remaining_bytes is None else min(MAX_EXTRACTED_BYTES, remaining_bytes)
        if total_uncompressed > quota:
            raise StagingError("wheel excede la cuota de extracción")
        for member in members:
            name = member.filename.replace("\\", "/")
            if not name or name.startswith("/") or ".." in Path(name).parts:
                raise StagingError(f"wheel con ruta insegura: {name}")
            if member.flag_bits & 0x1:
                raise StagingError(f"wheel cifrado no permitido: {name}")
            if member.file_size and member.compress_size == 0:
                raise StagingError(f"wheel con tamaño comprimido inválido: {name}")
            if member.compress_size and member.file_size / member.compress_size > MAX_COMPRESSION_RATIO:
                raise StagingError(f"wheel con relación de compresión insegura: {name}")
            # ZIP symlink entries must not become files in the evidence tree.
            if ((member.external_attr >> 16) & 0o170000) == 0o120000:
                raise StagingError(f"wheel con symlink: {name}")
            # Keep the package and its license metadata, but avoid extracting
            # arbitrary executable entry points into a runtime directory.
            destination = (target / name).resolve()
            if not destination.is_relative_to(target.resolve()):
                raise StagingError(f"wheel fuera de staging: {name}")
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                extracted.append(name)
    return extracted


def build(lock_path: Path, staging: Path, *, require_complete: bool = False) -> int:
    if not _is_ci():
        raise StagingError("staging con red solo puede ejecutarse dentro de CI")
    spec = json.loads(lock_path.read_text("utf-8"))
    if spec.get("release_enabled") is not False or any(spec.get("feature_gates", {}).get(k) is not False for k in ("pdfium", "paddle_ocr", "poppler_payload", "security_boundary")):
        raise StagingError("los gates deben permanecer cerrados durante staging")
    artifacts = spec.get("artifacts")
    names = spec.get("build_inputs")
    if not isinstance(artifacts, dict) or not isinstance(names, list) or not names:
        raise StagingError("build_inputs inválidos")
    expected_total = sum(
        artifact.get("size_bytes", 0)
        for name in names
        if isinstance((artifact := artifacts.get(name)), dict)
    )
    if expected_total > MAX_TOTAL_DOWNLOAD_BYTES:
        raise StagingError("build_inputs exceden la cuota acumulada de descarga")
    staging.mkdir(parents=True, exist_ok=True)
    downloads = staging / "downloads"
    packages = staging / "packages"
    verified: list[dict] = []
    blocked: list[dict] = []
    excluded: list[dict] = []
    extracted_bytes = 0
    for name in names:
        artifact = artifacts.get(name)
        if not isinstance(artifact, dict):
            raise StagingError(f"asset no declarado: {name}")
        status = artifact.get("status")
        if status != "verified_upstream_pypi":
            raise StagingError(f"build input bloqueado: {name} ({status})")
        filename = artifact.get("filename")
        if not isinstance(filename, str):
            raise StagingError(f"asset sin filename: {name}")
        record = _download_verified(artifact, downloads / filename)
        record["name"] = name
        verified.append(record)
        if filename.endswith(".whl"):
            extracted = _safe_extract_wheel(
                downloads / filename,
                packages / name,
                remaining_bytes=MAX_EXTRACTED_BYTES - extracted_bytes,
            )
            record["extracted_files"] = len(extracted)
            extracted_bytes += sum((packages / name / item).stat().st_size for item in extracted)
    for name, artifact in artifacts.items():
        if isinstance(artifact, dict) and str(artifact.get("status", "")).startswith("blocked_"):
            blocked.append({"name": name, "status": artifact["status"], "reason": artifact.get("blocked_reason", "")})
        elif isinstance(artifact, dict) and artifact.get("status") == "excluded_from_release_reference":
            excluded.append({"name": name, "status": artifact["status"], "reason": artifact.get("excluded_reason", "")})
    (staging / "STAGING-STATUS.json").write_text(json.dumps({
        "schema": SCHEMA, "production": False, "release_enabled": False,
        "verified_inputs": verified, "blocked_assets": blocked, "excluded_references": excluded,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (staging / "BLOCKED-ASSETS.md").write_text("# Assets bloqueados\n\n" + "\n".join(
        f"- **{item['name']}** (`{item['status']}`): {item['reason']}" for item in blocked
    ) + "\n", encoding="utf-8")
    if require_complete and blocked:
        raise StagingError("release incompleto: existen assets bloqueados; no se habilita producción")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, default=Path(__file__).parents[1] / "ocr-release.lock.json")
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args(argv)
    try:
        return build(args.lock.resolve(strict=True), args.staging.resolve(), require_complete=args.require_complete)
    except (OSError, ValueError, json.JSONDecodeError, StagingError) as exc:
        print(f"FAIL closed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
