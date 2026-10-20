"""Lossless directory migration, collisions, retries and both storage writers."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import check_bookmarks as base

storage = base.load('storage')
quickStore = base.load('quickFindStore')


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix='data-migration-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'extraSettings'

    def seed(self):
        values = {}
        for name in storage.DATA_FILES:
            for suffix in ('', '.bak'):
                key = name + suffix
                values[key] = ('Türkçe kayıt: ' + key).encode('utf-8')
                (self.root / key).write_bytes(values[key])
        return values

    def test_first_install_only_creates_data_directory(self):
        result = storage.prepareDataDirectory(self.root)
        self.assertEqual(result, self.directory)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_all_four_files_move_byte_for_byte_and_second_run_is_idempotent(self):
        values = self.seed()
        for _ in range(2):
            storage.prepareDataDirectory(self.root)
            for name, content in values.items():
                self.assertFalse((self.root / name).exists())
                self.assertEqual((self.directory / name).read_bytes(), content)
        self.assertEqual(len(list(self.directory.iterdir())), 4)

    def test_unrelated_files_and_bundled_language_data_are_not_moved(self):
        for name in ('nvda.ini', 'translationLanguages.json', 'otherAddon.json'):
            (self.root / name).write_text('unrelated', encoding='utf-8')
        storage.prepareDataDirectory(self.root)
        self.assertEqual(len(list(self.root.iterdir())), 4)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_matching_files_are_deduplicated_without_touching_new_contents(self):
        values = self.seed()
        self.directory.mkdir()
        for name, content in values.items():
            (self.directory / name).write_bytes(content)
        storage.prepareDataDirectory(self.root)
        self.assertEqual(len(list(self.root.iterdir())), 1)
        self.assertFalse((self.directory / 'legacy').exists())

    def test_conflicting_records_and_backups_are_preserved_without_overwrite(self):
        values = self.seed()
        self.directory.mkdir()
        for name in values:
            (self.directory / name).write_bytes(b'new authoritative data')
        for _ in range(2):
            storage.prepareDataDirectory(self.root)
        preserved = list((self.directory / 'legacy').iterdir())
        self.assertEqual(len(preserved), 4)
        self.assertCountEqual([file.read_bytes() for file in preserved], values.values())
        for name in values:
            self.assertEqual((self.directory / name).read_bytes(), b'new authoritative data')
            self.assertFalse((self.root / name).exists())

    def test_existing_preserved_conflict_is_reused(self):
        name = storage.DATA_FILES[0]
        self.directory.mkdir()
        (self.directory / name).write_bytes(b'new')
        for _ in range(2):
            (self.root / name).write_bytes(b'old')
            storage.prepareDataDirectory(self.root)
        self.assertEqual(len(list((self.directory / 'legacy').iterdir())), 1)

    def test_failed_move_keeps_source_and_retry_completes(self):
        values = self.seed()
        rename = storage.os.rename
        attempts = []
        def fail_second(source, destination):
            attempts.append(source)
            if len(attempts) == 2:
                raise PermissionError('simulated locked file')
            return rename(source, destination)
        with patch.object(storage.os, 'rename', side_effect=fail_second):
            with self.assertRaisesRegex(base.Error, 'write'):
                storage.prepareDataDirectory(self.root)
        for name, content in values.items():
            found = self.directory / name if (self.directory / name).exists() else self.root / name
            self.assertEqual(found.read_bytes(), content)
        storage.prepareDataDirectory(self.root)
        self.assertFalse(any((self.root / name).exists() for name in values))

    def test_path_obstruction_does_not_modify_legacy_files(self):
        values = self.seed()
        self.directory.write_bytes(b'not a directory')
        with self.assertRaisesRegex(base.Error, 'write'):
            storage.prepareDataDirectory(self.root)
        for name, content in values.items():
            self.assertEqual((self.root / name).read_bytes(), content)

    def test_unsupported_filename_cannot_escape_directory(self):
        for name in ('../nvda.ini', 'nvda.ini', '/absolute.json'):
            with self.assertRaises(ValueError):
                storage.dataPath(self.root, name)
        self.assertFalse(self.directory.exists())

    def test_bookmark_save_and_backup_use_new_directory_after_migration(self):
        name = storage.DATA_FILES[0]
        old = base.Store(self.root / name)
        identity = old.save('Test', 'web', 'https://example.org/')
        before = (self.root / name).read_bytes()
        path = storage.dataPath(self.root, name)
        new = base.Store(path)
        self.assertEqual(new.load()[0]['id'], identity)
        new.save('Updated', 'web', 'https://example.org/', identity)
        self.assertEqual(path.with_suffix('.json.bak').read_bytes(), before)
        self.assertFalse((self.root / name).exists())

    def test_damaged_legacy_data_is_preserved_and_reported(self):
        name = storage.DATA_FILES[1]
        (self.root / name).write_bytes(b'damaged data')
        path = storage.dataPath(self.root, name)
        with self.assertRaisesRegex(base.Error, 'damaged'):
            quickStore.QuickFindStore(path).load()
        self.assertEqual(path.read_bytes(), b'damaged data')

    def test_quick_find_shortcut_and_backup_survive_migration_and_edit(self):
        anchors = base.load('quickFindAnchor')
        name = storage.DATA_FILES[1]
        old = quickStore.QuickFindStore(self.root / name)
        old.load()
        entry = anchors.capture('Başlık\nMenü\nSon', 'Menü', 7, 'web:https://example.org/', 'Test')
        entry['shortcut'] = 'kb:alt+s'
        identity = old.saveAnchor(entry)
        before = (self.root / name).read_bytes()
        path = storage.dataPath(self.root, name)
        new = quickStore.QuickFindStore(path)
        saved = new.load()[0]
        self.assertEqual(saved['id'], identity)
        self.assertEqual(saved['shortcut'], 'kb:alt+s')
        saved = dict(saved, name='Updated')
        new.saveAnchor(saved, identity)
        self.assertEqual(path.with_suffix('.json.bak').read_bytes(), before)
        self.assertFalse((self.root / name).exists())


if __name__ == '__main__':
    unittest.main()
