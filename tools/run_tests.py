"""Run regression tests in their documented NVDA mock initialization order."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
MODULES = (
    'check_translation', 'check_automatic_translation', 'check_existing_features',
    'check_numpad_navigation', 'check_skip_blank_lines', 'check_text_statistics',
    'check_clipboard_inspector', 'check_bookmarks', 'check_quick_find',
    'check_extra_settings_storage',
)

if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
