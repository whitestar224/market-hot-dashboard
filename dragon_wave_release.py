"""Read-only, whole-case publication gate for the v91 strategy release."""

import json
import re
import threading
from pathlib import Path

CURRENT_VERSION = "v91"
PREVIOUS_VERSION = "v90"
ENGINE_SHA256 = "a9d45737f1bd6f69a69a894ba35ec72e11c129cbbfd7057de69ef3a9c19535b4"
INTERVALS = ("5m", "15m", "1h", "4h", "1d")
_manifests = {}
_lock = threading.Lock()


def read_manifest(root, version):
    path = Path(root) / version / "manifest.json"
    try:
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        with _lock:
            cached = _manifests.get(str(path))
            if cached and cached[0] == signature:
                return cached[1]
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                return {}
            _manifests[str(path)] = (signature, value)
            return value
    except (OSError, ValueError):
        return {}


def complete_case(root, version, pair, start, end, market, stage, engine_sha256=None):
    manifest = read_manifest(root, version)
    if manifest.get("version") != version:
        return False
    checkpoint = (manifest.get("cases") or {}).get("|".join((version, pair, start, end, market, stage))) or {}
    if checkpoint.get("state") != "complete":
        return False
    if engine_sha256 and checkpoint.get("engineSha256") != engine_sha256:
        return False
    for interval in INTERVALS:
        record = (manifest.get("records") or {}).get("|".join((version, pair, start, end, interval, market, stage))) or {}
        if record.get("contextComplete") is not True or not set(INTERVALS).issubset(record.get("contextIntervals") or []):
            return False
        if engine_sha256 and record.get("engineSha256") != engine_sha256:
            return False
        if any(record.get(key) != value for key, value in {
            "pair": pair, "start": start, "end": end, "interval": interval,
            "market": market, "mainWaveStage": stage,
        }.items()):
            return False
        filename = record.get("file") or ""
        if not re.fullmatch(r"[0-9a-f]{64}\.json\.gz", filename):
            return False
        try:
            if (Path(root) / version / filename).stat().st_size != record.get("bytes") or not record.get("bytes"):
                return False
        except OSError:
            return False
    return True


def resolve_case(root, pair, start, end, market="futures", stage="active"):
    args = (pair, start, end, market, stage)
    ready = complete_case(root, CURRENT_VERSION, *args, engine_sha256=ENGINE_SHA256)
    selected = CURRENT_VERSION if ready else (
        PREVIOUS_VERSION if complete_case(root, PREVIOUS_VERSION, *args) else None
    )
    manifest = read_manifest(root, CURRENT_VERSION)
    status = manifest.get("status") or {}
    checkpoint = (manifest.get("cases") or {}).get("|".join((CURRENT_VERSION, *args))) or {}
    needs_attention = not ready and checkpoint.get("state") == "partial" and bool(status.get("finishedAt"))
    return {
        "requestedVersion": CURRENT_VERSION, "selectedVersion": selected,
        "pending": not ready, "available": selected is not None,
        "engineSha256": ENGINE_SHA256 if ready else None,
        "intervals": list(INTERVALS),
        "pair": pair, "start": start, "end": end, "market": market, "stage": stage,
        "needsAttention": needs_attention,
        "progress": {"processedCases": status.get("processedCases", 0),
            "totalCases": status.get("totalCases", 0), "state": status.get("state", "queued")},
    }
