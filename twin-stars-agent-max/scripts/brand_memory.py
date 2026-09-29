"""Evidence-backed local memory; recommendations never override current instructions.

Only explicit feedback or a hash-bound result can create a memory. Scope is
structural, not inferred from prose. Performance entries are descriptive only;
this store cannot establish design-to-performance causality.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from v6_storage import atomic_json, file_lock, read_json, utc_now


SCHEMA_VERSION = "6.0"
SCOPES = {"brand", "campaign", "asset"}
KINDS = {"preference", "rejection", "failure", "approved_pattern", "performance"}
STATUSES = {"active", "superseded", "invalidated"}
RECORD_FIELDS = {
    "schema_version", "record_id", "brand_id", "project_id", "campaign_id",
    "asset_id", "scope", "kind", "content", "applicability", "source", "status",
    "created_at", "supersedes", "performance_interpretation",
}


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _source(value, verify_evidence=True):
    if not isinstance(value, dict):
        raise ValueError("source must be an object")
    source = deepcopy(value)
    _text(source.get("reference"), "source.reference")
    if source.get("type") == "explicit_user_feedback":
        _text(source.get("quote"), "source.quote")
    elif source.get("type") == "verified_result":
        binding = source.get("evidence_binding")
        if not isinstance(binding, dict):
            raise ValueError("verified_result requires source.evidence_binding")
        path = Path(_text(binding.get("path"), "source.evidence_binding.path"))
        digest = _text(binding.get("sha256"), "source.evidence_binding.sha256")
        if not path.is_absolute() or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("evidence_binding requires an absolute path and SHA-256 hex digest")
        if verify_evidence:
            if not path.is_file():
                raise ValueError("evidence_binding.path must be an existing absolute file")
            if sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("evidence_binding.sha256 does not match the evidence file")
    else:
        raise ValueError("memory source must be explicit_user_feedback or verified_result")
    return source


def _scope(record):
    if record.get("scope") not in SCOPES or record.get("kind") not in KINDS:
        raise ValueError("memory scope or kind is invalid")
    for key in ("campaign_id", "asset_id"):
        if record.get(key) is not None:
            _text(record[key], key)
    if record["scope"] == "brand" and (record.get("campaign_id") or record.get("asset_id")):
        raise ValueError("brand scope cannot carry campaign or asset restrictions")
    if record["scope"] == "campaign":
        _text(record.get("campaign_id"), "campaign_id for campaign scope")
        if record.get("asset_id"):
            raise ValueError("campaign scope cannot carry an asset restriction")
    if record["scope"] == "asset":
        _text(record.get("asset_id"), "asset_id for asset scope")


def _new_record(value):
    if not isinstance(value, dict):
        raise ValueError("memory record must be an object")
    unknown = set(value) - RECORD_FIELDS
    if unknown:
        raise ValueError(f"unsupported memory fields: {sorted(unknown)}")
    record = deepcopy(value)
    if record.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ValueError("legacy memory requires an explicit migration")
    record["schema_version"] = SCHEMA_VERSION
    record.setdefault("record_id", f"MEM-{uuid4().hex}")
    for key in ("record_id", "brand_id", "project_id", "content", "applicability"):
        _text(record.get(key), key)
    for key in ("campaign_id", "asset_id"):
        record.setdefault(key, None)
    _scope(record)
    if record.get("status", "active") != "active":
        raise ValueError("new memories must be active; use correction or invalidation")
    record["status"] = "active"
    record["source"] = _source(record.get("source"))
    if record.get("supersedes") is not None:
        _text(record["supersedes"], "supersedes")
        if record["source"]["type"] != "explicit_user_feedback":
            raise ValueError("only explicit user feedback may supersede a memory")
    if record["kind"] == "performance":
        if record["source"]["type"] != "verified_result":
            raise ValueError("performance requires a verified result, not an unverified report")
        if record.get("performance_interpretation", "descriptive_only") != "descriptive_only":
            raise ValueError("memory cannot establish performance causality")
        record["performance_interpretation"] = "descriptive_only"
    elif "performance_interpretation" in record:
        raise ValueError("performance_interpretation is only valid for performance records")
    # Use receipt time so caller-supplied timestamps cannot outrank later feedback.
    record["created_at"] = utc_now()
    return record


def _load(path):
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "revision": 0, "records": [], "history": []}
    data = read_json(path)
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("memory store must use schema 6.0; legacy files are not rewritten")
    if type(data.get("revision")) is not int or data["revision"] < 0:
        raise ValueError("invalid memory revision")
    if not isinstance(data.get("records"), list) or not isinstance(data.get("history"), list):
        raise ValueError("invalid memory records or history")
    ids = set()
    for record in data["records"]:
        if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("invalid stored memory record")
        record_id = _text(record.get("record_id"), "stored record_id")
        if record_id in ids or record.get("status") not in STATUSES:
            raise ValueError("duplicate memory id or invalid status")
        ids.add(record_id)
        for key in ("brand_id", "project_id", "content", "applicability", "created_at"):
            _text(record.get(key), f"stored {key}")
        _scope(record)
        _source(record.get("source"), verify_evidence=False)
        if record["kind"] == "performance" and (
            record["source"]["type"] != "verified_result"
            or record.get("performance_interpretation") != "descriptive_only"
        ):
            raise ValueError("stored performance must be verified and descriptive only")
    return data


def _boundary(record):
    boundary = (record["brand_id"], record["scope"])
    if record["scope"] != "brand":
        boundary += (record["project_id"], record.get("campaign_id"))
    if record["scope"] == "asset":
        boundary += (record["asset_id"],)
    return boundary


def _find(data, record_id, brand_id):
    for record in data["records"]:
        if record["record_id"] == record_id and record["brand_id"] == brand_id:
            return record
    raise ValueError("memory record not found in this brand")


def _commit(path, data, action, record, previous=None, **details):
    data["revision"] += 1
    data["history"].append({
        "revision": data["revision"], "action": action, "record_id": record["record_id"],
        "at": utc_now(), "previous": deepcopy(previous), "record": deepcopy(record), **details,
    })
    atomic_json(path, data)
    return deepcopy(record)


def record_memory(memory_root: Path, record: dict) -> dict:
    """Append validated memory, optionally superseding one same-scope record.

    A correction can originate in a later project for a brand-wide rule. Campaign
    and asset corrections must retain the original project and exact scope.
    """
    candidate = _new_record(record)
    root = Path(memory_root)
    path = root / "memory.json"
    # ponytail: one local store lock; shard by brand only if write contention matters.
    with file_lock(root / ".memory.lock"):
        data = _load(path)
        if any(item["record_id"] == candidate["record_id"] for item in data["records"]):
            raise ValueError("memory record_id already exists; history cannot be overwritten")
        previous = None
        if candidate.get("supersedes"):
            target = _find(data, candidate["supersedes"], candidate["brand_id"])
            if _boundary(target) != _boundary(candidate):
                raise ValueError("a correction cannot widen or change memory scope")
            if target["status"] != "active":
                raise ValueError("only an active memory can be superseded")
            previous = deepcopy(target)
            target.update(status="superseded", superseded_by=candidate["record_id"])
        data["records"].append(candidate)
        return _commit(path, data, "corrected" if previous else "recorded", candidate, previous)


def query_memory(memory_root: Path, brand_id: str, project_id=None,
                 campaign_id=None, asset_id=None) -> list[dict]:
    """Return applicable active recommendations in newest-first insertion order.

    No project context returns only brand rules. Applicability prose is retained
    for the agent to check against the current brief; it grants no authority over it.
    """
    _text(brand_id, "brand_id")
    for name, value in (("project_id", project_id), ("campaign_id", campaign_id), ("asset_id", asset_id)):
        if value is not None:
            _text(value, name)
    if (campaign_id or asset_id) and not project_id:
        raise ValueError("campaign or asset queries require project_id")
    path = Path(memory_root) / "memory.json"
    # Writers replace the complete file atomically; readers never see half a revision.
    data = _load(path)
    result = []
    for record in reversed(data["records"]):
        if record["brand_id"] != brand_id or record["status"] != "active":
            continue
        if record["scope"] != "brand":
            if record["project_id"] != project_id:
                continue
            if record.get("campaign_id") and record["campaign_id"] != campaign_id:
                continue
        if record["scope"] == "asset" and record.get("asset_id") != asset_id:
            continue
        if record["source"]["type"] == "verified_result":
            try:
                _source(record["source"])
            except (OSError, ValueError):
                # Keep the original result in history, but never reuse stale evidence.
                continue
        result.append(deepcopy(record))
    return result


def invalidate_memory(memory_root: Path, record_id: str, brand_id: str,
                      reason: str, source: dict) -> dict:
    """Preserve a memory and its evidence while withdrawing its recommendation."""
    for name, value in (("record_id", record_id), ("brand_id", brand_id), ("reason", reason)):
        _text(value, name)
    provenance = _source(source)
    root = Path(memory_root)
    path = root / "memory.json"
    with file_lock(root / ".memory.lock"):
        data = _load(path)
        record = _find(data, record_id, brand_id)
        if record["status"] != "active":
            raise ValueError("only an active memory can be invalidated")
        previous = deepcopy(record)
        record.update(status="invalidated", invalidation={
            "reason": reason, "source": provenance, "at": utc_now(),
        })
        return _commit(path, data, "invalidated", record, previous)
