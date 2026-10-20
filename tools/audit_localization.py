"""Check all runtime messages, interpolation fields and both help documents."""
import ast
import gettext
from html.parser import HTMLParser
from pathlib import Path
from string import Formatter
import json
import re
from manifest import readManifest

ROOT = Path(__file__).resolve().parents[1] / 'addon'

def readCatalog(path):
    catalog = {}
    key = value = field = None
    for raw in path.read_text(encoding='utf-8-sig').splitlines() + ['']:
        line = raw.strip()
        if not line:
            if key is not None:
                assert key not in catalog, ('duplicate message', path, key)
                assert value is not None, ('missing msgstr', path, key)
                catalog[key] = value
            key = value = field = None
        elif line.startswith('#'):
            continue
        elif line.startswith('msgid '):
            key = ast.literal_eval(line[6:]); field = 'key'
        elif line.startswith('msgstr '):
            value = ast.literal_eval(line[7:]); field = 'value'
        elif line.startswith('"'):
            if field == 'key': key += ast.literal_eval(line)
            elif field == 'value': value += ast.literal_eval(line)
            else: raise AssertionError((path, line))
        else: raise AssertionError((path, line))
    return catalog

def fields(text):
    return sorted((field, conversion, spec) for literal, field, spec, conversion in Formatter().parse(text)
                  if field is not None)

def messages():
    result = set()
    for path in ROOT.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == '_':
                assert node.args and isinstance(node.args[0], ast.Constant), ('dynamic gettext message', path, node.lineno)
                result.add(node.args[0].value)
    return result

class Help(HTMLParser):
    def __init__(self):
        super().__init__(); self.ids = set(); self.links = []; self.names = []; self.text = []
        self.language = None; self.sectionCount = 0
        self.stack = []
    def handle_starttag(self, tag, pairs):
        attrs = dict(pairs)
        if tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):
            self.stack.append(tag)
        if tag == 'html': self.language = attrs['lang']
        if tag == 'section': self.sectionCount += 1
        if 'id' in attrs:
            assert attrs['id'] not in self.ids, attrs['id']
            self.ids.add(attrs['id'])
        if 'href' in attrs: self.links.append(attrs['href'])
        self.names.extend(attrs.get('aria-labelledby', '').split())
    def handle_data(self, text): self.text.append(text)
    def handle_endtag(self, tag):
        assert self.stack and self.stack.pop() == tag, ('unbalanced help markup', tag)

def audit(requireEnglish=True):
    source = messages()
    result = {}
    manifest = readManifest(ROOT / 'manifest.ini')
    for language in (('tr','en') if requireEnglish else ('tr',)):
        po = ROOT / f'locale/{language}/LC_MESSAGES/nvda.po'
        catalog = readCatalog(po)
        assert f'Language: {language}\n' in catalog['']
        assert f"Project-Id-Version: extraSettings {manifest['version']}\n" in catalog['']
        regional = gettext.translation('nvda', localedir=ROOT / 'locale', languages=[language + ('_TR' if language == 'tr' else '_US')])
        missing = sorted(source - catalog.keys())
        assert not missing, (language, 'untranslated messages', missing)
        assert set(catalog) - {''} == source, (language, 'obsolete catalog entries')
        for key in source:
            assert catalog[key], (language, 'empty translation', key)
            assert fields(key) == fields(catalog[key]), (language, 'placeholder mismatch', key)
        with po.with_suffix('.mo').open('rb') as stream:
            compiled = gettext.GNUTranslations(stream)
        for key in source:
            assert compiled.gettext(key) == catalog[key], (language, 'stale MO', key)
            assert regional.gettext(key) == catalog[key], (language, 'regional fallback failed', key)
        helpFile = ROOT / f'doc/{language}/readme.html'
        help = Help(); help.feed(helpFile.read_text(encoding='utf-8-sig'))
        assert not help.stack, (language, 'unclosed help markup', help.stack)
        assert help.language == language
        assert all(name in help.ids for name in help.names)
        for link in help.links:
            if link.startswith('#'): assert link[1:] in help.ids
            elif link.startswith('mailto:'): assert link == 'mailto:m.aykurt38@gmail.com', link
            elif not link.startswith(('https:', 'http:')): assert (helpFile.parent / link).is_file(), link
        text = ' '.join(help.text)
        markup = helpFile.read_text(encoding='utf-8-sig')
        nav = re.search(r'<nav\b.*?</nav>',markup,re.S).group(0)
        assert re.findall(r'href="#([^"]+)"',nav) == re.findall(r'<section aria-labelledby="([^"]+)"',markup), (language,'contents order differs from guide')
        for name in ('bookmarks.py','bookmarksDialog.py','quickFind.py','quickFindDialog.py'):
            tree = ast.parse((ROOT/'globalPlugins/extraSettings'/name).read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='_':
                    key = node.args[0].value
                    if key.endswith('.') and '{' not in key:
                        assert catalog[key] in text, (language,'undocumented new-feature notification',key)
        for key in ('Assigned shortcut','Assign shortcut','Change shortcut','Remove assigned shortcut',
                    'Folder path','Web address','Quick find','Bookmarks'):
            assert catalog[key] in text, (language,'missing current control label',key)
        assert manifest['version'] in text
        assert catalog['NVDA Extra Settings'] in text
        if language == 'tr':
            assert '<strong>NVDA Ek Ayarlar</strong>' not in helpFile.read_text(encoding='utf-8-sig')
        for key in ('Choose a supported target language in Extra Settings.', 'Choose a target language.',
                    'Protected fields cannot be translated.', 'The line at the caret could not be read.',
                    'This line is too long to translate. The limit is 5000 characters.',
                    'Translation is already in progress.', 'Key selection canceled.',
                    'Open the context menu', 'History entries'):
            assert catalog[key] in text, (language, 'undocumented interface label or message', key)
        for gesture in ('NVDA+Shift+T', 'NVDA+Shift+O', 'NVDA+Shift+K', 'NVDA+Shift+C', 'NVDA+Shift+Y', 'NVDA+Shift+H', 'NVDA+G', 'Windows+C'):
            assert gesture in text, (language, 'missing shortcut', gesture)
        for key in ('Inspect clipboard', 'Enable clipboard inspection in Extra Settings first.', 'The clipboard is empty.', 'The clipboard does not contain supported text, a table or files.', 'Clipboard inspection is already in progress.', 'The clipboard content is too large to inspect.', 'The clipboard could not be read. Please try again.', 'The file could not be accessed. It may have been moved or deleted.', 'Announce word and character counts', 'Enable word and character counts in Extra Settings first.', 'Select some text first.', 'Text counting is already in progress.', 'The selected text could not be counted.', 'Skip blank lines on web pages', 'No more non-blank lines in this direction.', 'Translation', 'Line translation — {shortcut}', 'Automatic translation — {shortcut}',
                    'Enable translation in Extra Settings first.', 'Enable line translation in Extra Settings first.',
                    'Enable automatic translation in Extra Settings first.', 'No shortcut assigned'):
            label = catalog[key].replace(' — {shortcut}', '')
            assert label in text, (language, 'help differs from interface', label)
        for key in ('Automatic translation on.', 'Automatic translation off.', 'The automatic translation queue is full.',
                    'Automatic translation paused. Turn it off and on to start a new session.',
                    'The translation request could not be completed. Please try again.',
                    'The translation service did not respond in time.', 'The translation service returned an unreadable response.'):
            assert catalog[key] in text, (language, 'undocumented message', key)
        for key in ('Cut.', 'Copied.', 'Pasted.', 'No selected data was found to cut.',
                    'No selected data was found to copy.', 'No data was found to paste.',
                    'The clipboard could not be checked right now.', 'The selection could not be checked.',
                    'Added to clipboard.', 'No selected text was found to append.',
                    'The selected text could not be read.', 'The clipboard contains non-text data. Copy text first.',
                    'Could not append to the clipboard.', 'There are no history entries yet.',
                    'Could not copy to the clipboard.', 'Speech history cleared.',
                    'Enable speech history in Extra Settings first.', 'Enter a whole number from 1 to 1000.',
                    'Another key capture is already in progress.', 'Choose a shortcut using Set key first.',
                    'Release the shortcut keys and try again.'):
            assert catalog[key] in text, (language, 'undocumented existing feature message', key)
        navTree = ast.parse((ROOT / 'globalPlugins/extraSettings/numpadNavigation.py').read_text(encoding='utf-8-sig'))
        navKeys = next(ast.literal_eval(node.value) for node in navTree.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == '_NAVIGATION_KEYS' for target in node.targets))
        labels = dict(home='Home', end='End', pageUp='Page Up', pageDown='Page Down',
                      upArrow='Yukarı ok' if language == 'tr' else 'Up arrow',
                      downArrow='Aşağı ok' if language == 'tr' else 'Down arrow',
                      leftArrow='Sol ok' if language == 'tr' else 'Left arrow',
                      rightArrow='Sağ ok' if language == 'tr' else 'Right arrow')
        for key, target in navKeys.items():
            assert key[-1] + ': ' + labels[target] in text, (language, 'wrong keypad mapping', key, target)
        localized = (ROOT / f'locale/{language}/manifest.ini').read_text(encoding='utf-8-sig')
        description = 'translation' if language == 'en' else 'Çeviri'
        assert description in localized
        localizedDescription = readManifest(ROOT / f'locale/{language}/manifest.ini')['description']
        assert '\n' in localizedDescription and 'm.aykurt38@gmail.com' in localizedDescription
        assert localizedDescription.startswith('Bu eklenti:' if language == 'tr' else 'This add-on:')
        assert help.sectionCount == 20, (language, 'missing help section', help.sectionCount)
        headings = re.findall(r'<h2 id="[^"]+">(.*?)</h2>', markup)
        assert len(headings) == 20
        assert all(heading.startswith(f'{number}. ') for number, heading in enumerate(headings, 1)), (language, 'chapter numbering')
        for chapter, block in enumerate(re.findall(r'<section\b.*?</section>', markup, re.S), 1):
            subheadings = re.findall(r'<h3>(.*?)</h3>', block)
            assert all(heading.startswith(f'{chapter}.{number}. ') for number, heading in enumerate(subheadings, 1)), (
                language, 'subsection numbering', chapter)
        manualSections = ('kurulum', 'kisayollar', 'veriler', 'sorun-giderme') if language == 'tr' else (
            'installation', 'keyboard-commands', 'stored-data', 'troubleshooting')
        assert all(identifier in help.ids for identifier in manualSections), (language, 'missing manual chapter')
        assert '<caption>' in markup and markup.count('scope="col"') == 3 and markup.count('scope="row"') == 8, (
            language, 'keyboard command table accessibility')
        result[language] = dict(runtimeMessages=len(source), catalogMessages=len(catalog)-1,
                                helpSections=help.sectionCount)
    languages = json.loads((ROOT / 'globalPlugins/extraSettings/translationLanguages.json').read_text(encoding='utf-8-sig'))['languages']
    assert len(languages) == 249
    assert len({item['code'] for item in languages}) == 249
    assert all(item['tr'].strip() and item['en'].strip() for item in languages)
    return result

if __name__ == '__main__':
    print(json.dumps(audit(), ensure_ascii=True, indent=2))
