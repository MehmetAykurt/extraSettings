"""Build a deterministic NVDA package and the developer's update.json format."""
import argparse
import ast
import gettext
import hashlib
import json
from pathlib import Path
import re
import struct
import zipfile
from audit_localization import readCatalog, audit
from manifest import readManifest

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / 'addon'


def compile_catalog(language):
    path = ADDON / f'locale/{language}/LC_MESSAGES/nvda.po'
    catalog = readCatalog(path)
    keys = sorted(catalog)
    originals = [key.encode('utf-8') for key in keys]
    translations = [catalog[key].encode('utf-8') for key in keys]
    count = len(keys)
    original_offset = 28 + 16 * count
    translation_offset = original_offset + sum(len(value) + 1 for value in originals)
    blob = bytearray(struct.pack('<7I', 0x950412DE, 0, count, 28, 28 + 8 * count, 0, 0))
    for values, offset in ((originals, original_offset), (translations, translation_offset)):
        for value in values:
            blob.extend(struct.pack('<2I', len(value), offset))
            offset += len(value) + 1
    blob.extend(b'\0'.join(originals) + b'\0')
    blob.extend(b'\0'.join(translations) + b'\0')
    mo = path.with_suffix('.mo')
    mo.write_bytes(blob)
    with mo.open('rb') as stream:
        translated = gettext.GNUTranslations(stream)
    assert all(translated.gettext(key) == value for key, value in catalog.items())


def build(repository, description):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Repository must have the form owner/name')
    manifest = readManifest(ADDON / 'manifest.ini')
    version = manifest.get('version')
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', version):
        raise ValueError('Invalid manifest version')
    for path in ADDON.rglob('*.py'):
        compile(path.read_text(encoding='utf-8-sig'), str(path.relative_to(ROOT)), 'exec')
    for language in ('tr', 'en'):
        compile_catalog(language)
    print(audit())
    assert (ADDON / 'LICENSE').read_bytes() == (ROOT / 'LICENSE').read_bytes()
    output = ROOT / 'dist' / f'extraSettings-{version}.nvda-addon'
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(ADDON.rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'):
                continue
            if path.is_symlink():
                raise ValueError('Symbolic links are not included in the package')
            info = zipfile.ZipInfo(path.relative_to(ADDON).as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert {'manifest.ini', 'LICENSE', 'globalPlugins/extraSettings/storage.py'} <= set(archive.namelist())
    metadata = {
        'surum': version,
        'link': f'https://github.com/{repository}/releases/download/v{version}/{output.name}',
        'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
        'aciklama': description if description is not None else readManifest(ADDON / 'locale/tr/manifest.ini')['description'],
    }
    (ROOT / 'update.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(output)
    print('SHA-256: ' + metadata['sha256'])
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', default='MehmetAykurt/extraSettings')
    parser.add_argument('--description', help='Optional release summary; defaults to the complete Turkish manifest description.')
    args = parser.parse_args()
    build(args.repository, args.description)
