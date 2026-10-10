"""Where the profile lives between runs. One interface, three backends.

NativeMemoryStore: state.memory (Gravity, <= state.max_kb). This is what ships.
LocalFileStore:    tests and local runs.
PlatformStore:     placeholder for get_store; NOT usable until Gravity confirms it exists for our agents.
The profile is compacted (and compressed) to fit, dropping the oldest detail first."""
from __future__ import annotations

import base64
import json
import zlib
from pathlib import Path
from typing import Any, Protocol

SCHEMA = 1


class ProfileStore(Protocol):
    def load(self) -> dict[str, Any]: ...
    def dump(self, profile: dict[str, Any]) -> dict[str, Any]: ...


def pack(profile: dict[str, Any]) -> str:
    return base64.b64encode(zlib.compress(json.dumps(profile, separators=(",", ":"), sort_keys=True).encode(), 9)).decode()


def unpack(blob: str) -> dict[str, Any]:
    return json.loads(zlib.decompress(base64.b64decode(blob)).decode())


def packed_kb(profile: dict[str, Any]) -> float:
    return len(pack(profile)) / 1024


def fit(profile: dict[str, Any], max_kb: float) -> tuple[dict[str, Any], list[str]]:
    """Shrink until the packed profile fits. Order: item titles/urls of the oldest items first,
    then cached outputs, then oldest item rows. Seen-id keys are kept longest: they stop duplicates.
    Returns (profile, notes) where notes say exactly what was dropped."""
    p = json.loads(json.dumps(profile))
    notes: list[str] = []
    if packed_kb(p) <= max_kb:
        return p, notes
    items = sorted(p.get("items", []), key=lambda r: r.get("date", ""))
    for stage in ("title", "cached", "rows"):
        if stage == "title":
            for r in items:
                if packed_kb(p) <= max_kb:
                    break
                r["title"] = r.get("title", "")[:30]
                r.pop("url_extra", None)
            notes.append("shortened titles of older items")
        elif stage == "cached":
            if packed_kb(p) > max_kb and p.get("cached"):
                p["cached"] = {}
                notes.append("dropped cached on-demand outputs")
        else:
            dropped = 0
            while packed_kb(p) > max_kb and items:
                items.pop(0)
                p["items"] = items
                dropped += 1
                if dropped % 25 == 0:
                    continue
            if dropped:
                notes.append(f"dropped detail of the {dropped} oldest items (their ids are still remembered)")
        if packed_kb(p) <= max_kb:
            break
    p["items"] = items
    return p, notes


class NativeMemoryStore:
    def __init__(self, memory: dict[str, Any] | None, max_kb: int = 256, margin: float = 0.92):
        self.memory = memory or {}
        self.budget = max_kb * margin

    def load(self) -> dict[str, Any]:
        blob = self.memory.get("profile")
        if not blob:
            return {}
        try:
            p = unpack(blob)
            return p if p.get("schema") == SCHEMA else {}
        except Exception:  # corrupt memory: start fresh and say so upstream
            return {"_corrupt": True}

    def dump(self, profile: dict[str, Any]) -> dict[str, Any]:
        profile = dict(profile, schema=SCHEMA)
        p, notes = fit(profile, self.budget)
        p["compaction"] = notes
        # re-fit once more after adding notes
        p, _ = fit(p, self.budget)
        return {"profile": pack(p)}


class LocalFileStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def dump(self, profile: dict[str, Any]) -> dict[str, Any]:
        self.path.write_text(json.dumps(dict(profile, schema=SCHEMA), indent=1, sort_keys=True))
        return {"profile_path": str(self.path)}


class PlatformStore:
    """Reserved for langgraph get_store(). Raises until Gravity confirms it is available to our agents."""

    def load(self) -> dict[str, Any]:
        raise NotImplementedError("platform store not confirmed by Gravity")

    def dump(self, profile: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("platform store not confirmed by Gravity")
