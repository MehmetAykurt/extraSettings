"""Read-only Windows clipboard snapshots and offline, inert previews.

Use pointer-sized native handles on both 32-bit and 64-bit NVDA. Release the
clipboard before parsing HTML or querying file metadata. Never open file
contents, enumerate directories, execute HTML or fetch external resources.
"""
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
import locale
import os
from pathlib import PureWindowsPath
import re
import stat

import addonHandler

addonHandler.initTranslation()

MAX_BYTES = 16 * 1024 * 1024
MAX_FILES = 10000
CF_UNICODETEXT = 13
CF_HDROP = 15

_user = ctypes.WinDLL('user32', use_last_error=True)
_kernel = ctypes.WinDLL('kernel32', use_last_error=True)
_shell = ctypes.WinDLL('shell32', use_last_error=True)
_ole = ctypes.WinDLL('ole32', use_last_error=True)

def _function(dll, name, result, args):
    func = getattr(dll, name)
    func.restype = result
    func.argtypes = args
    return func

_open = _function(_user, 'OpenClipboard', wintypes.BOOL, [wintypes.HWND])
_close = _function(_user, 'CloseClipboard', wintypes.BOOL, [])
_available = _function(_user, 'IsClipboardFormatAvailable', wintypes.BOOL, [wintypes.UINT])
_data = _function(_user, 'GetClipboardData', wintypes.HANDLE, [wintypes.UINT])
_count = _function(_user, 'CountClipboardFormats', ctypes.c_int, [])
_register = _function(_user, 'RegisterClipboardFormatW', wintypes.UINT, [wintypes.LPCWSTR])
_lock = _function(_kernel, 'GlobalLock', ctypes.c_void_p, [wintypes.HANDLE])
_unlock = _function(_kernel, 'GlobalUnlock', wintypes.BOOL, [wintypes.HANDLE])
_size = _function(_kernel, 'GlobalSize', ctypes.c_size_t, [wintypes.HANDLE])
_queryFile = _function(_shell, 'DragQueryFileW', wintypes.UINT,
                     [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT])
_coInitialize = _function(_ole, 'CoInitializeEx', ctypes.c_long, [ctypes.c_void_p, wintypes.DWORD])
_coUninitialize = _function(_ole, 'CoUninitialize', None, [])

class _SHFILEINFO(ctypes.Structure):
    _fields_ = [('hIcon', wintypes.HANDLE), ('iIcon', ctypes.c_int), ('dwAttributes', wintypes.DWORD),
                ('szDisplayName', wintypes.WCHAR * 260), ('szTypeName', wintypes.WCHAR * 80)]

_fileInfo = _function(_shell, 'SHGetFileInfoW', ctypes.c_size_t,
    [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(_SHFILEINFO), wintypes.UINT, wintypes.UINT])


class ClipboardTooLarge(ValueError):
    pass


@dataclass(frozen=True)
class Snapshot:
    text: str | None = None
    html: bytes | None = None
    files: tuple = ()
    hasData: bool = False


@dataclass(frozen=True)
class Preview:
    content: str
    isHtml: bool = False


def _readBytes(formatId):
    handle = _data(formatId)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    length = _size(handle)
    if length > MAX_BYTES:
        raise ClipboardTooLarge
    if not length:
        return b''
    pointer = _lock(handle)
    if not pointer:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(pointer, length)
    finally:
        _unlock(handle)


def _readFiles():
    handle = _data(CF_HDROP)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    count = _queryFile(handle, 0xFFFFFFFF, None, 0)
    if count > MAX_FILES:
        raise ClipboardTooLarge
    paths = []
    for index in range(count):
        length = _queryFile(handle, index, None, 0)
        if not 0 < length <= 32767:
            raise ValueError('Invalid clipboard file path length')
        buffer = ctypes.create_unicode_buffer(length + 1)
        copied = _queryFile(handle, index, buffer, length + 1)
        if copied != length:
            raise OSError('Clipboard file path could not be read')
        paths.append(buffer.value)
    return tuple(paths)


def readSnapshot():
    """Called only on invocation, on the worker thread; do not change data."""
    if not _open(None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if _available(CF_HDROP):
            return Snapshot(files=_readFiles(), hasData=True)
        text = html = None
        textError = None
        if _available(CF_UNICODETEXT):
            try:
                text = _readBytes(CF_UNICODETEXT).decode('utf-16-le').partition('\0')[0]
            except (OSError, ValueError) as exc:
                textError = exc
        htmlId = _register('HTML Format')
        if htmlId and _available(htmlId):
            try:
                html = _readBytes(htmlId)
            except (OSError, ValueError):
                if text is None:
                    raise
        if textError is not None and html is None:
            raise textError
        return Snapshot(text=text, html=html, hasData=bool(_count()))
    finally:
        _close()


def htmlFragment(raw):
    """Honor UTF-8 byte offsets; do not expose uncopied surrounding context."""
    raw = raw.partition(b'\0')[0]
    header = raw[:4096]
    fields = {name: int(value) for name, value in re.findall(
        rb'(?im)^(StartFragment|EndFragment):[ \t]*(-?\d+)', header)}
    start, end = fields.get(b'StartFragment'), fields.get(b'EndFragment')
    if start is not None and end is not None:
        if not 0 <= start <= end <= len(raw):
            raise ValueError('Invalid clipboard HTML offsets')
        fragment = raw[start:end]
    else:
        startMarker, endMarker = b'<!--StartFragment-->', b'<!--EndFragment-->'
        start, end = raw.find(startMarker), raw.find(endMarker)
        if not 0 <= start < end:
            raise ValueError('Clipboard HTML fragment is missing')
        fragment = raw[start+len(startMarker):end]
    return fragment.decode('utf-8-sig')


class _InertHTML(HTMLParser):
    allowed = frozenset('table thead tbody tfoot tr th td caption colgroup col p div br ul ol li strong b em i u s pre code h1 h2 h3 h4 h5 h6 blockquote span sup sub'.split())
    blocked = frozenset('script style head title iframe frame object embed svg math audio video canvas noscript template'.split())
    void = frozenset(('br', 'col'))
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output = []
        self.text = []
        self.stack = []
        self.suppressed = []
        self.table = False
        self.hasTableTag = False
        self.hasRow = False

    def handle_starttag(self, tag, attrs):
        if tag in self.blocked:
            self.suppressed.append(tag)
            return
        if self.suppressed or tag not in self.allowed:
            return
        if tag in ('table', 'tr', 'td', 'th', 'thead', 'tbody', 'tfoot'):
            self.table = True
        self.hasTableTag |= tag == 'table'
        self.hasRow |= tag == 'tr'
        clean = []
        if tag in ('td', 'th', 'col', 'colgroup'):
            for key, value in attrs:
                if key in ('colspan', 'rowspan', 'span') and value and value.isascii() and value.isdigit() and 1 <= int(value) <= 1000:
                    clean.append(f' {key}="{int(value)}"')
                elif tag == 'th' and key == 'scope' and value in ('row', 'col', 'rowgroup', 'colgroup'):
                    clean.append(f' scope="{value}"')
        self.output.append('<' + tag + ''.join(clean) + '>')
        if tag in ('br', 'p', 'div', 'tr', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.text.append('\n')
        if tag not in self.void:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.suppressed:
            if tag == self.suppressed[-1]:
                self.suppressed.pop()
            return
        if tag in self.stack:
            while self.stack:
                current = self.stack.pop()
                self.output.append('</' + current + '>')
                if current == tag:
                    break
        if tag in ('td', 'th'):
            self.text.append('\t')
        elif tag in ('p', 'div', 'tr', 'li', 'pre'):
            self.text.append('\n')

    def handle_data(self, data):
        if not self.suppressed:
            self.output.append(escape(data))
            self.text.append(data)

    def result(self):
        while self.stack:
            self.output.append('</' + self.stack.pop() + '>')
        content = ''.join(self.output)
        if self.table and not self.hasTableTag:
            if not self.hasRow and re.search(r'<t[dh](?:\s|>)', content):
                content = '<tr>' + content + '</tr>'
            content = '<table>' + content + '</table>'
        return content, ''.join(self.text).strip(), self.table


def fileType(path, folder):
    if folder:
        return _("Folder")
    info = _SHFILEINFO()
    # USEFILEATTRIBUTES avoids opening or accessing the actual file for its
    # description. It also avoids loading icons or running file contents.
    if _fileInfo(str(path), 0x80, ctypes.byref(info), ctypes.sizeof(info), 0x10 | 0x400) and info.szTypeName:
        return info.szTypeName
    extension = PureWindowsPath(path).suffix
    return _("{extension} file").format(extension=extension.lstrip('.').upper()) if extension else _("File")


def formatSize(size):
    if size < 1024:
        return _("{size} bytes").format(size=size)
    for power, unit in ((4, 'TiB'), (3, 'GiB'), (2, 'MiB'), (1, 'KiB')):
        if size >= 1024**power:
            amount = locale.format_string('%.2f', size / 1024**power)
            return _("{amount} {unit} ({size} bytes)").format(amount=amount, unit=unit, size=size)


def filesPreview(paths):
    # SHGetFileInfo requires COM on the calling worker. If the thread already
    # uses another apartment model, leave that model and its lifetime alone.
    result = _coInitialize(None, 2)
    if result < 0 and result != ctypes.c_long(0x80010106).value:
        raise OSError('File metadata initialization failed')
    try:
        return _filesPreview(paths)
    finally:
        if result >= 0:
            _coUninitialize()


def _filesPreview(paths):
    blocks = [_("Files on clipboard: {count}").format(count=len(paths))]
    for number, path in enumerate(paths, 1):
        name = PureWindowsPath(path).name or path
        lines = [_("File {number}").format(number=number), _("Name: {name}").format(name=name)]
        error = None
        try:
            info = os.stat(path)
            folder = stat.S_ISDIR(info.st_mode)
            kind = fileType(path, folder)
            size = _("Not calculated for folders.") if folder else formatSize(info.st_size)
        except (OSError, ValueError):
            kind = size = _("Unavailable")
            error = _("The file could not be accessed. It may have been moved or deleted.")
        lines.extend((_("Type: {type}").format(type=kind), _("Size: {size}").format(size=size),
                      _("Path: {path}").format(path=path)))
        if error:
            lines.append(error)
        blocks.append('\n'.join(lines))
    return Preview('\n\n'.join(blocks))


def makePreview(snapshot):
    if snapshot.files:
        return filesPreview(snapshot.files)
    parsed = None
    if snapshot.html:
        try:
            parser = _InertHTML()
            parser.feed(htmlFragment(snapshot.html))
            parser.close()
            parsed = parser.result()
        except (ValueError, UnicodeError):
            pass
    if parsed is not None and parsed[2]:
        return Preview(parsed[0], True)
    if snapshot.text is not None and snapshot.text != '':
        return Preview(snapshot.text)
    if parsed is not None and parsed[1]:
        return Preview(parsed[1])
    if not snapshot.hasData or snapshot.text == '' and not snapshot.html:
        return Preview(_("The clipboard is empty."))
    return Preview(_("The clipboard does not contain supported text, a table or files."))
