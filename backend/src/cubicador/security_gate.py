"""Gate de privilegios, llaves y auditoría para el worker OCR.

La definición describe lo que el modelo puede tocar. El hash aprobado vive
fuera del JSON para que editar el mismo archivo no pueda habilitar el gate.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .security import SecurityViolation


TRUSTED_SECURITY_GATE_SHA256 = "0" * 64
_IDENTITY_KEYS = {"container", "integrity_level", "run_as_administrator", "allowed_privileges",
                  "network_capabilities", "credential_access"}
_KEY_KEYS = {"session_token", "session_token_lifetime", "per_job_nonce", "persistent_secrets",
             "command_line_secrets", "logged_secrets"}
_RESOURCE_KEYS = {"allowed_read", "allowed_write", "denied"}
_AUDIT_KEYS = {"hash_chain", "max_bytes", "required_events", "windows_event_ids",
               "required_fields", "secret_fields_forbidden"}
_EVIDENCE_KEYS = {"appcontainer_no_capabilities", "restricted_token", "vendor_acl_read_only",
                  "workspace_acl_private", "firewall_block_outbound", "wfp_allowed_event_capture",
                  "wfp_blocked_event_capture", "ephemeral_token_rotation", "windows_10_smoke",
                  "windows_11_smoke"}
_ALLOWED_READ = {"signed-vendor-bundle", "job-input"}
_ALLOWED_WRITE = {"private-job-workspace"}
_DENIED = {"internet", "lan", "loopback-network", "user-profile", "registry-write",
           "shell", "child-process-outside-job", "clipboard", "camera", "microphone",
           "user-credentials", "files-outside-workspace"}
_AUDIT_EVENTS = {"worker-start", "gate-decision", "outbound-connection-allowed",
                 "outbound-connection-blocked", "file-access-denied", "worker-stop"}
_AUDIT_FIELDS = {"timestamp", "job_id", "worker_pid", "executable_sha256", "decision",
                 "protocol", "destination_ip", "destination_port", "rule_id"}


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"clave duplicada: {key}")
        result[key] = value
    return result


def _load(path: Path) -> tuple[dict, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("gate no es un archivo regular")
    stat = path.stat()
    if stat.st_size <= 0 or stat.st_size > 64 * 1024 or stat.st_nlink != 1:
        raise ValueError("gate fuera de cuota o enlazado")
    if getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
        raise ValueError("gate no puede ser reparse point")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    spec = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    if not isinstance(spec, dict):
        raise ValueError("gate inválido")
    return spec, digest


def validate_security_gate(path: Path) -> list[str]:
    """Valida el contrato. Un gate cerrado es válido, pero no autoriza OCR."""
    try:
        spec, _digest = _load(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return [f"gate ilegible: {exc}"]
    errors: list[str] = []
    if set(spec) != {"schema_version", "approved", "worker_identity", "keys", "resources", "audit", "evidence"}:
        errors.append("estructura del gate inválida")
        return errors
    identity, keys, resources, audit, evidence = (spec[k] for k in
        ("worker_identity", "keys", "resources", "audit", "evidence"))
    if spec["schema_version"] != 1 or type(spec["approved"]) is not bool:
        errors.append("versión/aprobación inválida")
    if not isinstance(identity, dict) or set(identity) != _IDENTITY_KEYS:
        errors.append("identidad incompleta")
    elif (identity["container"] != "appcontainer" or identity["integrity_level"] != "low"
          or identity["run_as_administrator"] is not False or identity["credential_access"] is not False
          or identity["network_capabilities"] != []
          or identity["allowed_privileges"] != ["SeChangeNotifyPrivilege"]):
        errors.append("identidad no aplica mínimo privilegio")
    if not isinstance(keys, dict) or set(keys) != _KEY_KEYS:
        errors.append("contrato de llaves incompleto")
    elif (keys["session_token"] != "random-256-bit-memory-only"
          or keys["session_token_lifetime"] != "process" or keys["per_job_nonce"] is not True
          or any(keys[name] is not False for name in ("persistent_secrets", "command_line_secrets", "logged_secrets"))):
        errors.append("llaves no son efímeras")
    if (not isinstance(resources, dict) or set(resources) != _RESOURCE_KEYS
            or not all(isinstance(resources.get(name), list) for name in _RESOURCE_KEYS)
            or set(resources.get("allowed_read", [])) != _ALLOWED_READ
            or set(resources.get("allowed_write", [])) != _ALLOWED_WRITE
            or set(resources.get("denied", [])) != _DENIED):
        errors.append("recursos prohibidos incompletos")
    if not isinstance(audit, dict) or set(audit) != _AUDIT_KEYS:
        errors.append("contrato de auditoría incompleto")
    else:
        events = set(audit["required_events"]) if isinstance(audit["required_events"], list) else set()
        fields = set(audit["required_fields"]) if isinstance(audit["required_fields"], list) else set()
        if (audit["hash_chain"] is not True or audit["secret_fields_forbidden"] is not True
                or type(audit["max_bytes"]) is not int or audit["max_bytes"] != 10 * 1024 * 1024
                or audit["windows_event_ids"] != [5156, 5157]
                or events != _AUDIT_EVENTS or fields != _AUDIT_FIELDS):
            errors.append("auditoría de red incompleta")
    if not isinstance(evidence, dict) or set(evidence) != _EVIDENCE_KEYS or any(type(v) is not bool for v in evidence.values()):
        errors.append("evidencias inválidas")
    elif spec["approved"] is True and not all(evidence.values()):
        errors.append("gate aprobado sin todas las evidencias")
    return errors


def require_security_gate(path: Path | None = None,
                          trusted_sha256: str = TRUSTED_SECURITY_GATE_SHA256) -> dict:
    gate = path or Path(__file__).resolve().parents[2] / "security-gate.json"
    errors = validate_security_gate(gate)
    if errors:
        raise SecurityViolation("Gate de seguridad inválido: " + "; ".join(errors))
    spec, digest = _load(gate)
    if (not re.fullmatch(r"[0-9a-f]{64}", trusted_sha256) or trusted_sha256 == "0" * 64
            or digest != trusted_sha256 or spec["approved"] is not True
            or not all(spec["evidence"].values())):
        raise SecurityViolation("Gate de seguridad OCR no aprobado")
    return spec
