"""Validated bookmark records and atomic, conflict-aware persistence."""
import json
import ntpath
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit
import uuid


class BookmarkError(Exception):
    pass


def normalize(kind, target):
    target = target.strip()
    if not target or any(ord(c) < 32 for c in target):
        raise BookmarkError("target")
    if kind == "web":
        try:
            parsed = urlsplit(target)
            if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError()
            parsed.port
            if any(c.isspace() for c in target) or "\\" in target:
                raise ValueError()
        except ValueError:
            raise BookmarkError("url") from None
        return target
    if kind == "folder":
        target = os.path.expandvars(target.strip('"'))
        drive, tail = ntpath.splitdrive(target)
        rooted = tail.startswith(("\\", "/")) or (drive.startswith("\\\\") and not tail)
        if not drive or not rooted or target.startswith(("\\\\?", "\\\\.")) or any(c in target for c in '<>|*?'):
            raise BookmarkError("folder")
        return ntpath.normpath(target)
    raise BookmarkError("target")


def targetKey(kind, target):
    target = normalize(kind, target)
    if kind == "folder":
        return kind, ntpath.normcase(target)
    parsed = urlsplit(target)
    return kind, (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, parsed.fragment)


class BookmarkStore:
    def __init__(self, path):
        self.path = Path(path)
        self.entries = []
        self._original = None

    def load(self):
        try:
            raw = self.path.read_bytes() if self.path.exists() else None
            data = json.loads(raw) if raw is not None else {"version": 1, "entries": []}
            if data.get("version") != 1 or not isinstance(data.get("entries"), list):
                raise ValueError()
            entries = data["entries"]
            seenIds, seenTargets = set(), set()
            for entry in entries:
                if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) for k in ("id", "name", "kind", "target")):
                    raise ValueError()
                if not entry["id"] or not entry["name"].strip() or entry["id"] in seenIds:
                    raise ValueError()
                key = targetKey(entry["kind"], entry["target"])
                if key in seenTargets:
                    raise ValueError()
                seenIds.add(entry["id"]); seenTargets.add(key)
        except (ValueError, TypeError, AttributeError, BookmarkError):
            raise BookmarkError("damaged") from None
        except OSError:
            raise BookmarkError("read") from None
        self._original = raw
        self.entries = entries
        return entries

    def save(self, name, kind, target, entryId=None):
        name = name.strip()
        if not name or any(ord(c) < 32 for c in name):
            raise BookmarkError("name")
        target = normalize(kind, target)
        key = targetKey(kind, target)
        if any(e["id"] != entryId and targetKey(e["kind"], e["target"]) == key for e in self.entries):
            raise BookmarkError("duplicate")
        if entryId is not None and not any(e["id"] == entryId for e in self.entries):
            raise BookmarkError("changed")
        entry = dict(id=entryId or uuid.uuid4().hex, name=name, kind=kind, target=target)
        updated = [entry if e["id"] == entryId else dict(e) for e in self.entries]
        if entryId is None:
            updated.append(entry)
        self._write(updated)
        return entry["id"]

    def remove(self, entryId):
        updated = [dict(e) for e in self.entries if e["id"] != entryId]
        if len(updated) == len(self.entries):
            raise BookmarkError("changed")
        self._write(updated)

    def _write(self, entries):
        temporary = None
        try:
            current = self.path.read_bytes() if self.path.exists() else None
            if current != self._original:
                raise BookmarkError("changed")
            raw = (json.dumps({"version": 1, "entries": entries}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=self.path.name, suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            # Keep the previous valid contents for manual recovery.
            if current is not None:
                self.path.with_suffix(".json.bak").write_bytes(current)
            os.replace(temporary, self.path)
            temporary = None
        except OSError:
            raise BookmarkError("write") from None
        finally:
            if temporary is not None:
                try: os.unlink(temporary)
                except OSError: pass
        self.entries = entries
        self._original = raw
