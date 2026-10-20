"""Single user-data directory and lossless migration of legacy Windows files."""
import hashlib
import os
from pathlib import Path
from .bookmarkStore import BookmarkError


DATA_FILES = ("extraSettingsBookmarks.json", "extraSettingsQuickFind.json")


def _relocate(source, destination, directory):
    if not source.exists():
        return
    # Windows rename is atomic on the same volume and refuses to overwrite an
    # existing destination. A competing migration therefore cannot destroy data.
    try:
        os.rename(source, destination)
        return
    except FileExistsError:
        pass
    content = source.read_bytes()
    if destination.read_bytes() == content:
        source.unlink()
        return
    # The new location is authoritative. Keep a conflicting legacy file intact
    # in the same data directory, without attempting to merge document records.
    legacy = directory / "legacy"
    legacy.mkdir(exist_ok=True)
    preserved = legacy / (source.name + "." + hashlib.sha256(content).hexdigest())
    try:
        os.rename(source, preserved)
    except FileExistsError:
        if preserved.read_bytes() != content:
            raise OSError("Conflicting legacy data could not be preserved")
        source.unlink()


def prepareDataDirectory(configPath):
    root = Path(configPath)
    directory = root / "extraSettings"
    try:
        directory.mkdir(exist_ok=True)
        for name in DATA_FILES:
            # Move backups first. If migration is interrupted, another attempt
            # completes it without regenerating or replacing either backup.
            for filename in (name + ".bak", name):
                _relocate(root / filename, directory / filename, directory)
    except OSError:
        raise BookmarkError("write") from None
    return directory


def dataPath(configPath, filename):
    if filename not in DATA_FILES:
        raise ValueError("Unsupported Extra Settings data filename")
    return prepareDataDirectory(configPath) / filename
