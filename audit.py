"""Tamper-evident audit log.

Every entry is written as one JSON line carrying a ``prev`` hash (the hash
of the previous entry) and its own ``hash``. Editing or deleting any line
breaks the chain, which ``verify()`` detects.

This does not stop someone with file access from rewriting the whole file
from scratch, but it makes silent edits detectable, which is the point of
an append-only audit trail.
"""

import hashlib
import json
import os
from pathlib import Path


def _canonical(entry: dict) -> str:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _digest(entry: dict, prev: str) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    body["prev"] = prev
    return hashlib.sha256(
        (prev + "\n" + _canonical(body)).encode("utf-8")).hexdigest()


def _read_entries(path) -> list:
    p = Path(path)
    if not p.exists():
        return []
    entries = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            entries.append(None)
    return entries


def last_hash(path) -> str:
    entries = _read_entries(path)
    for entry in reversed(entries):
        if isinstance(entry, dict) and "hash" in entry:
            return entry["hash"]
        if isinstance(entry, dict):
            # A legacy line (written before hashing existed): restart the
            # chain from here rather than refusing to append.
            return ""
        return ""
    return ""


def append(path, entry: dict) -> dict:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    prev = last_hash(p)
    out = dict(entry)
    out["prev"] = prev
    out["hash"] = _digest(out, prev)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(out, sort_keys=True) + "\n")
    try:
        if os.name == "posix":
            os.chmod(p, 0o600)
    except OSError:
        pass
    return out


def verify(path) -> tuple[bool, int | None]:
    """Return (ok, first_bad_index). Legacy lines without hashes are skipped."""
    entries = _read_entries(path)
    hashed = [(i, e) for i, e in enumerate(entries)
              if isinstance(e, dict) and "hash" in e]
    if not hashed:
        return True, None
    prev = hashed[0][1].get("prev", "")
    for i, entry in hashed:
        if entry.get("prev", "") != prev:
            return False, i
        if entry.get("hash") != _digest(entry, prev):
            return False, i
        prev = entry["hash"]
    return True, None
