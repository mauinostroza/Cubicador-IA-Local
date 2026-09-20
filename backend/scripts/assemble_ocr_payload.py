"""Ensamblado OCR offline, exacto y fail-closed."""
from __future__ import annotations
import argparse, ctypes, errno, hashlib, json, os, shutil, stat, tempfile, zipfile
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 2
VERIFIED_STATUSES = frozenset({"verified_upstream_pypi", "verified_upstream_hf", "verified_release_input"})
REQUIRED_COMPONENTS = frozenset({"runner", "runtime", "models"})
MAX_FILES, MAX_BYTES, MAX_RATIO = 100_000, 3 * 1024**3, 200
class AssemblyError(RuntimeError): pass

def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out: raise AssemblyError(f"clave JSON duplicada: {key}")
        out[key] = value
    return out

def _load(path):
    value = json.loads(path.read_text("utf-8"), object_pairs_hook=_pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(AssemblyError(f"número inválido: {value}")))
    if not isinstance(value, dict): raise AssemblyError("lock inválido")
    return value

def _sha_stream(stream):
    digest, total = hashlib.sha256(), 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk); total += len(chunk)
    return digest.hexdigest(), total

def _sha(path):
    with path.open("rb") as stream: return _sha_stream(stream)[0]

def _reparse(path):
    if path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)()): return True
    try: return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except OSError: return False

def _clean_existing_ancestors(path):
    current = path
    while not current.exists() and not current.is_symlink(): current = current.parent
    while True:
        if _reparse(current): return False
        if current.parent == current: return True
        current = current.parent

def _relative(value, label):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value: raise AssemblyError(f"ruta inválida: {label}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts: raise AssemblyError(f"ruta inválida: {label}")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    for segment in path.parts:
        if (not segment or segment.endswith((".", " "))
                or any(ord(char) < 32 or char in '<>:"|?*' for char in segment)
                or segment.split(".", 1)[0].upper() in reserved):
            raise AssemblyError(f"ruta Windows no portable: {label}")
    return path.as_posix()

def _target(root, relative):
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()): raise AssemblyError("destino fuera del payload")
    return target

def _reserve(state, entries):
    """Reserva nombres/tamaños globales antes de cualquier escritura."""
    pending = []
    for relative, size in entries:
        folded = relative.casefold()
        if folded in state["names"] or any(folded == prior[0] for prior in pending):
            raise AssemblyError(f"colisión de destino: {relative}")
        pending.append((folded, size))
    new_files = state["files"] + len(pending); new_bytes = state["bytes"] + sum(size for _, size in pending)
    if new_files > MAX_FILES or new_bytes > MAX_BYTES: raise AssemblyError("payload final excede cuota global")
    state["names"].update(name for name, _ in pending); state["files"], state["bytes"] = new_files, new_bytes

def _source(root, relative):
    path = root / relative
    if any(_reparse(parent) for parent in (path, *path.parents) if parent == root or parent.is_relative_to(root)):
        raise AssemblyError(f"reparse no permitido: {relative}")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd); raise AssemblyError(f"entrada no regular o enlazada: {relative}")
    return os.fdopen(fd, "rb")

def _contract(lock_path):
    spec = _load(lock_path)
    if spec.get("release_enabled") is not False or any(spec.get("feature_gates", {}).values()):
        raise AssemblyError("release y gates deben estar cerrados")
    assembly, artifacts = spec.get("payload_assembly"), spec.get("artifacts")
    if (not isinstance(assembly, dict) or set(assembly) != {"schema_version", "offline_only", "inputs"}
            or assembly.get("schema_version") != 2 or assembly.get("offline_only") is not True):
        raise AssemblyError("contrato schema 2/offline_only inválido")
    inputs = assembly.get("inputs")
    if not isinstance(artifacts, dict) or not isinstance(inputs, list) or not inputs: raise AssemblyError("entradas inválidas")
    result, names, destinations, components = [], set(), set(), set()
    for raw in inputs:
        if not isinstance(raw, dict) or set(raw) != {"artifact", "component", "source", "destination", "kind"}:
            raise AssemblyError("entrada inválida")
        name, component, kind = raw["artifact"], raw["component"], raw["kind"]
        if not all(isinstance(x, str) for x in (name, component, kind)) or kind not in {"file", "wheel"}: raise AssemblyError("entrada inválida")
        source, destination = _relative(raw["source"], name), _relative(raw["destination"], name)
        if name in names or destination.casefold() in destinations: raise AssemblyError("entrada/destino duplicado")
        names.add(name); destinations.add(destination.casefold()); components.add(component)
        artifact = artifacts.get(name); status = artifact.get("status") if isinstance(artifact, dict) else None
        if status not in VERIFIED_STATUSES: raise AssemblyError(f"entrada bloqueada: {name} ({status})")
        digest, size, version = artifact.get("sha256"), artifact.get("size_bytes"), artifact.get("version")
        if (not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
                or not isinstance(size, int) or size <= 0 or not isinstance(version, str) or not version): raise AssemblyError(f"pin incompleto: {name}")
        result.append({**raw, "source": source, "destination": destination, "sha256": digest, "size": size, "version": version})
    if components != REQUIRED_COMPONENTS: raise AssemblyError("cierre de componentes incompleto")
    if len(result) > MAX_FILES or sum(x["size"] for x in result) > MAX_BYTES: raise AssemblyError("cuota excedida")
    return result

def _copy(root, item, temporary, state):
    with _source(root, item["source"]) as source:
        digest, size = _sha_stream(source)
        if (digest, size) != (item["sha256"], item["size"]): raise AssemblyError(f"pin no coincide: {item['artifact']}")
        _reserve(state, [(item["destination"], size)])
        source.seek(0); target = _target(temporary, item["destination"]); target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            shutil.copyfileobj(source, output, 1024 * 1024); output.flush(); os.fsync(output.fileno())
    if _sha(target) != item["sha256"]: raise AssemblyError("rehash de destino falló")
    return [(item["destination"], target)]

def _wheel(root, item, temporary, state):
    with _source(root, item["source"]) as raw:
        digest, size = _sha_stream(raw)
        if (digest, size) != (item["sha256"], item["size"]): raise AssemblyError(f"pin no coincide: {item['artifact']}")
        raw.seek(0)
        with zipfile.ZipFile(raw) as archive:
            infos, seen, total = archive.infolist(), set(), 0
            if len(infos) > MAX_FILES: raise AssemblyError("wheel excede archivos")
            for info in infos:
                stripped = info.filename.rstrip("/"); name = _relative(stripped, "wheel") if stripped else ""
                if not name or name.casefold() in seen: raise AssemblyError("wheel contiene rutas vacías/duplicadas")
                seen.add(name.casefold())
                if info.flag_bits & 1 or ((info.external_attr >> 16) & 0o170000) == 0o120000: raise AssemblyError("wheel cifrado o con symlink")
                if info.file_size and (not info.compress_size or info.file_size / info.compress_size > MAX_RATIO): raise AssemblyError("wheel con relación insegura")
                total += info.file_size
            if total > MAX_BYTES: raise AssemblyError("wheel excede cuota")
            output_entries = [(f"{item['destination'].rstrip('/')}/{info.filename}", info.file_size)
                              for info in infos if not info.is_dir()]
            _reserve(state, output_entries)
            extracted = []
            for info in infos:
                if info.is_dir(): continue
                relative = f"{item['destination'].rstrip('/')}/{info.filename}"
                target = _target(temporary, relative); target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024); output.flush(); os.fsync(output.fileno())
                extracted.append((relative, target))
            return extracted

def _fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    except OSError: pass

def _rename_no_replace(source, destination):
    """Publicación atómica sin reemplazo (Windows o renameat2 Linux)."""
    if os.name == "nt":
        os.rename(source, destination)  # Windows falla si el destino existe.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None: raise AssemblyError("plataforma sin publicación atómica NOREPLACE")
    result = renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    if result:
        code = ctypes.get_errno()
        if code == errno.EEXIST: raise FileExistsError(destination)
        raise OSError(code, os.strerror(code))

def assemble(lock_path, source_root, output):
    try: lock_info = lock_path.stat()
    except OSError as exc: raise AssemblyError("lock no disponible") from exc
    if (_reparse(lock_path) or lock_info.st_nlink != 1 or not stat.S_ISREG(lock_info.st_mode)
            or not _clean_existing_ancestors(lock_path) or _reparse(source_root)
            or not _clean_existing_ancestors(source_root) or not _clean_existing_ancestors(output)
            or output.exists() or output.is_symlink()): raise AssemblyError("origen/destino inseguro o existente")
    inputs = _contract(lock_path); source_root = source_root.resolve(strict=True); output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        files, component_files, versions = {}, {}, {}
        state = {"names": set(), "files": 0, "bytes": 0}
        for item in inputs:
            produced = (_wheel if item["kind"] == "wheel" else _copy)(source_root, item, temporary, state)
            versions.setdefault(item["component"], set()).add(f"{item['artifact']}@{item['version']}")
            for relative, path in produced:
                if relative.lower().endswith((".whl", ".tar", ".tar.gz", ".zip")): raise AssemblyError("archivo opaco prohibido")
                files[relative] = {"sha256": _sha(path), "size": path.stat().st_size, "component": item["component"], "artifact": item["artifact"]}
                component_files.setdefault(item["component"], []).append(relative)
        components, release = {}, {}
        for name in sorted(REQUIRED_COMPONENTS):
            version, members = "+".join(sorted(versions[name])), component_files[name]
            components[name] = {"version": version, "files": members, "limits": {"max_files": len(members), "max_bytes": sum(files[x]["size"] for x in members)}}
            release[f"{name}_version"] = version
        runner = next(x for x in inputs if x["component"] == "runner")["destination"]
        manifest = {"schema_version": 2, "production": False, "release_enabled": False, "release": release,
            "limits": {"max_files": len(files), "max_bytes": sum(x["size"] for x in files.values())},
            "components": components, "tools": {"paddle.runner": runner}, "files": files}
        encoded = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
        with (temporary / "toolchain-manifest.json").open("xb") as stream:
            stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
        with (temporary / "toolchain-manifest.sha256").open("x", encoding="ascii") as stream:
            stream.write(hashlib.sha256(encoded).hexdigest() + "\n"); stream.flush(); os.fsync(stream.fileno())
        _fsync_dir(temporary)
        if output.exists() or output.is_symlink(): raise AssemblyError("carrera de destino detectada")
        _rename_no_replace(temporary, output); _fsync_dir(output.parent); return manifest
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True); raise

def main(argv=None):
    parser = argparse.ArgumentParser(); parser.add_argument("--lock", type=Path, default=Path(__file__).parents[1]/"ocr-release.lock.json")
    parser.add_argument("--source-root", type=Path, required=True); parser.add_argument("--output", type=Path, required=True); args = parser.parse_args(argv)
    try: assemble(args.lock.resolve(strict=True), args.source_root, args.output)
    except (AssemblyError, OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as exc: print(f"FAIL closed: {exc}"); return 2
    print("OK payload schema 2; gates cerrados"); return 0
if __name__ == "__main__": raise SystemExit(main())
