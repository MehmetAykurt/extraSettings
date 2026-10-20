"""Persistent document anchors, with the atomic bookmark storage writer."""
import json
import re
import uuid
from .bookmarkStore import BookmarkStore, BookmarkError


def validShortcut(identifier):
    if not identifier:
        return True
    if not isinstance(identifier, str) or not identifier.startswith("kb:"):
        return False
    parts = identifier[3:].casefold().split("+")
    modifierNames = {"nvda", "alt", "control", "shift", "windows"}
    modifiers = set(parts) & modifierNames
    keys = [part for part in parts if part not in modifierNames]
    if not modifiers or len(keys) != 1 or len(parts) != len(set(parts)):
        return False
    key = keys[0]
    if not modifiers & {"nvda", "alt", "control"}:
        return False
    if not ((len(key) == 1 and key.isalnum()) or re.fullmatch(r"f(?:[1-9]|1[0-9]|2[0-4])", key)):
        return False
    if "nvda" not in modifiers and modifiers <= {"control", "shift"} and key in "acvxyzfosph":
        return False
    return True


def validate(entry):
    required = ("id", "name", "docKey", "document", "text", "before", "after", "digest", "shortcut")
    if not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) for key in required):
        raise ValueError()
    if not all(entry[key].strip() for key in ("id", "name", "docKey", "document", "text", "digest")):
        raise ValueError()
    if len(entry["text"]) > 1000 or len(entry["name"]) > 500 or not validShortcut(entry["shortcut"]):
        raise ValueError()
    if not all(type(entry.get(key)) is int and entry[key] >= 0 for key in ("offset", "ordinal")):
        raise ValueError()


class QuickFindStore(BookmarkStore):
    def load(self):
        try:
            raw = self.path.read_bytes() if self.path.exists() else None
            data = json.loads(raw) if raw is not None else {"version": 1, "entries": []}
            if data.get("version") != 1 or not isinstance(data.get("entries"), list):
                raise ValueError()
            seenIds, seenKeys = set(), set()
            for entry in data["entries"]:
                validate(entry)
                key = (entry["docKey"], entry["shortcut"])
                if entry["id"] in seenIds or (entry["shortcut"] and key in seenKeys):
                    raise ValueError()
                seenIds.add(entry["id"]); seenKeys.add(key)
        except (ValueError, TypeError, AttributeError):
            raise BookmarkError("damaged") from None
        except OSError:
            raise BookmarkError("read") from None
        self._original = raw
        self.entries = data["entries"]
        return self.entries

    def saveAnchor(self, entry, entryId=None):
        entry = dict(entry, id=entryId or uuid.uuid4().hex)
        entry["name"] = entry["name"].strip()
        try:
            validate(entry)
        except ValueError:
            raise BookmarkError("invalid") from None
        if entryId and not any(e["id"] == entryId for e in self.entries):
            raise BookmarkError("changed")
        for existing in self.entries:
            if existing["id"] == entryId or existing["docKey"] != entry["docKey"]:
                continue
            if entry["shortcut"] and entry["shortcut"] == existing["shortcut"]:
                raise BookmarkError("shortcut")
            if (existing["text"], existing["offset"], existing["digest"]) == (entry["text"], entry["offset"], entry["digest"]):
                raise BookmarkError("duplicate")
        updated = [entry if e["id"] == entryId else dict(e) for e in self.entries]
        if entryId is None:
            updated.append(entry)
        self._write(updated)
        return entry["id"]
