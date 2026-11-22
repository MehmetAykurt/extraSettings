"""Selective ZIP exports of an NVDA user configuration directory."""
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import zipfile


SETTINGS_PATHS = {
    "settings": ("nvda.ini",),
    "profiles": ("profiles", "profileTriggers.ini"),
    "gestures": ("gestures.ini",),
    "dictionaries": ("speechDicts",),
    "bookmarks": ("extraSettings/extraSettingsBookmarks.json",
                  "extraSettings/extraSettingsBookmarks.json.bak"),
    "quickFind": ("extraSettings/extraSettingsQuickFind.json",
                 "extraSettings/extraSettingsQuickFind.json.bak"),
}


class BackupCanceled(Exception):
    pass


class EmptyBackup(ValueError):
    pass


class ImportRollbackError(OSError):
    def __init__(self, recoveryPath):
        self.recoveryPath = recoveryPath
        super().__init__("Import rollback could not restore every file")


def _memberPath(info):
    name = info.filename
    if (not name or "\\" in info.orig_filename or name.startswith("/") or "\0" in info.orig_filename):
        raise ValueError("Invalid backup member")
    parts = name.rstrip("/").split("/")
    for part in parts:
        if (not part or part in (".", "..") or part.endswith((".", " "))
                or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
                or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])", part.split(".")[0])):
            raise ValueError("Invalid backup path")
    mode = info.external_attr >> 16
    if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise ValueError("Linked backup members cannot be imported")
    if info.flag_bits & 1:
        raise ValueError("Encrypted backups cannot be imported")
    relative = Path(*parts)
    allowedFiles = {path.casefold() for paths in SETTINGS_PATHS.values() for path in paths}
    normalized = "/".join(parts).casefold()
    first = parts[0].casefold()
    if info.is_dir():
        supported = (first in ("profiles", "speechdicts", "addons")
                     or normalized == "extrasettings")
    else:
        supported = (normalized in allowedFiles
                     or first in ("profiles", "speechdicts") and len(parts) >= 2
                     or first == "addons" and len(parts) >= 3)
    if not supported:
        raise ValueError("Unsupported backup member")
    return relative


def _safeDestination(root, relative):
    target = root
    for part in relative.parts:
        target = target / part
        try:
            metadata = target.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise OSError("Linked import destinations cannot be used")
    target.resolve().relative_to(root)
    return target


def importBackup(configPath, archivePath, cancel):
    """Validate and stage all files before replacing any configuration files.

    Keep originals on the same volume so failed or canceled imports can undo
    completed replacements. Files absent from the ZIP are preserved.
    """
    root = Path(configPath).resolve()
    root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".nvda-import-", dir=root))
    originals = stage / "old"
    incoming = stage / "new"
    applied = []
    createdDirectories = []
    preserveRecovery = False
    try:
        with zipfile.ZipFile(archivePath) as archive:
            selected = []
            seen = {}
            for info in archive.infolist():
                _checkCanceled(cancel)
                relative = _memberPath(info)
                key = relative.as_posix().casefold()
                if key in seen:
                    raise ValueError("Duplicate backup path")
                seen[key] = info.is_dir()
                target = _safeDestination(root, relative)
                if info.is_dir():
                    if target.exists() and not target.is_dir():
                        raise ValueError("Backup directory conflicts with a file")
                else:
                    if target.exists() and not target.is_file():
                        raise ValueError("Backup file conflicts with a directory")
                    selected.append((info, relative))
            for key in seen:
                parts = key.split("/")
                for end in range(1, len(parts)):
                    if seen.get("/".join(parts[:end])) is False:
                        raise ValueError("Backup file conflicts with a directory")
            if not selected:
                raise EmptyBackup()
            if sum(info.file_size for info, relative in selected) > shutil.disk_usage(root).free:
                raise OSError("Insufficient space for backup import")
            for info, relative in selected:
                _checkCanceled(cancel)
                staged = incoming / relative
                staged.parent.mkdir(parents=True, exist_ok=True)
                # Reading to EOF verifies each ZIP member's CRC before applying.
                with archive.open(info) as reader, staged.open("wb") as writer:
                    while True:
                        _checkCanceled(cancel)
                        data = reader.read(1024 * 1024)
                        if not data:
                            break
                        writer.write(data)
        try:
            for info, relative in selected:
                _checkCanceled(cancel)
                target = _safeDestination(root, relative)
                missing = []
                parent = target.parent
                while parent != root and not parent.exists():
                    missing.append(parent)
                    parent = parent.parent
                for directory in reversed(missing):
                    directory.mkdir()
                    createdDirectories.append(directory)
                previous = None
                if target.exists():
                    previous = originals / relative
                    previous.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, previous)
                os.replace(incoming / relative, target)
                applied.append((relative, previous))
            _checkCanceled(cancel)
        except Exception:
            errors = []
            for relative, previous in reversed(applied):
                try:
                    target = _safeDestination(root, relative)
                    if previous is None:
                        target.unlink()
                    else:
                        os.replace(previous, target)
                except (OSError, ValueError) as error:
                    errors.append(error)
            for directory in reversed(createdDirectories):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            if errors:
                preserveRecovery = True
                raise ImportRollbackError(originals) from None
            raise
        return len(applied)
    finally:
        if not preserveRecovery:
            shutil.rmtree(stage)


def documentsDirectory():
    """Resolve the Windows Documents folder, including redirected locations."""
    import ctypes
    from ctypes import wintypes
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    getFolder = shell.SHGetFolderPathW
    getFolder.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.HANDLE,
                         wintypes.DWORD, wintypes.LPWSTR]
    getFolder.restype = ctypes.c_long
    buffer = ctypes.create_unicode_buffer(260)
    if getFolder(None, 5, None, 0, buffer) != 0 or not buffer.value:
        raise OSError("Documents folder is unavailable")
    return Path(buffer.value)


def _checkCanceled(cancel):
    if cancel.is_set():
        raise BackupCanceled()


def _files(root, path, cancel):
    """Do not traverse junctions or symbolic links outside selected contents."""
    _checkCanceled(cancel)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
        raise OSError("Linked backup contents cannot be exported")
    path.resolve().relative_to(root)
    if stat.S_ISDIR(metadata.st_mode):
        for child in sorted(path.iterdir()):
            yield from _files(root, child, cancel)
    elif stat.S_ISREG(metadata.st_mode):
        yield path
    else:
        raise OSError("Unsupported backup file")


def exportBackup(configPath, destination, settings, addons, cancel):
    """Stream selected files to a temporary archive and publish only on success.

    addons contains directory names, not display labels or executable paths.
    All member names are relative to NVDA's user configuration directory.
    """
    root = Path(configPath).resolve()
    destination = Path(destination).resolve()
    # A backup inside its own source could recursively include itself.
    if destination == root or root in destination.parents:
        raise ValueError("Backup destination is inside the source")
    selected = []
    for key in settings:
        selected.extend((root / name, False) for name in SETTINGS_PATHS[key])
    for name in addons:
        if not name or name in (".", "..") or "/" in name or "\\" in name or ":" in name:
            raise ValueError("Invalid add-on directory")
        folder = root / "addons" / name
        if not folder.is_dir():
            raise OSError("Selected add-on is no longer available")
        selected.append((folder, True))
    if not selected:
        raise EmptyBackup()
    destination.mkdir(parents=True, exist_ok=True)
    temporary = None
    count = 0
    seen = set()
    try:
        with tempfile.NamedTemporaryFile(dir=destination, prefix=".nvda-backup-", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6, allowZip64=True) as archive:
            for path, required in selected:
                _checkCanceled(cancel)
                if not path.exists() and not path.is_symlink():
                    if required:
                        raise OSError("Selected add-on is no longer available")
                    continue
                for source in _files(root, path, cancel):
                    member = source.relative_to(root).as_posix()
                    if member in seen:
                        continue
                    seen.add(member)
                    before = source.stat()
                    info = zipfile.ZipInfo.from_file(source, arcname=member)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    with source.open("rb") as reader, archive.open(info, "w", force_zip64=True) as writer:
                        while True:
                            _checkCanceled(cancel)
                            data = reader.read(1024 * 1024)
                            if not data:
                                break
                            writer.write(data)
                    after = source.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise OSError("Backup source changed during export")
                    count += 1
        if not count:
            raise EmptyBackup()
        _checkCanceled(cancel)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        suffix = temporary.stem.removeprefix(".nvda-backup-")
        result = destination / f"NVDA-Backup-{stamp}-{suffix}.zip"
        # Windows rename refuses to overwrite an existing destination.
        os.rename(temporary, result)
        temporary = None
        return result
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
