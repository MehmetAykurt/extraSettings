# Extra Settings

[Türkçe ana sayfa](README.md)

This add-on:  

Web navigation cycling,  
Skipping blank lines on web pages,  
Clipboard announcements,  
Speech history,  
Assigning a shortcut in place of the Applications key,  
Appending text to the clipboard,  
Word and character counting,  
Inspecting clipboard text/table/file information,  
Folder and web page bookmarks,  
Using Quick find to navigate to saved text locations with document-specific shortcuts,  
Numeric keypad navigation,  
Brings these and other frequently needed features, including translation, to NVDA.  
You can enable the feature you want in Extra Settings under NVDA menu / Preferences / Settings;  
To request additional features, you can contact m.aykurt38@gmail.com.  

## Version and compatibility

- Add-on version: 2026.10.20
- Minimum and last tested NVDA version: 2026.2
- Interface and documentation: Turkish and English

## Developer

Mehmet Aykurt

[Mehmet Aykurt](https://mehmetaykurt.com.tr)

Contact: [m.aykurt38@gmail.com](mailto:m.aykurt38@gmail.com)

## Features

Web quick navigation cycling; skipping blank web lines; cut/copy/paste announcements; memory-only speech history; an Applications key substitute; appending selected text to the clipboard; sentence, word, character and blank-line counts; inspection of clipboard text, HTML tables and file information; folder and website bookmarks; Quick find locations with document-specific shortcuts; numeric keypad navigation; manual and automatic line translation.

## Installation

Open a released `.nvda-addon` package while NVDA is running, follow the installation prompts, and restart NVDA when requested. Open Preferences, Settings, Extra Settings. Enable the required options and choose Apply or OK.

[Download the installation package from Releases](https://github.com/MehmetAykurt/extraSettings/releases/latest). Add-on Store submission will be handled separately.

## Default keyboard commands

| Action | Shortcut |
| --- | --- |
| Show speech history | NVDA+G |
| Append to clipboard | Windows+C |
| Announce word and character counts | NVDA+Shift+K |
| Inspect clipboard | NVDA+Shift+C |
| Bookmarks | NVDA+Shift+Y |
| Quick find | NVDA+Shift+H |
| Translate current line | NVDA+Shift+T |
| Start or stop automatic translation | NVDA+Shift+O |

Enable the corresponding feature first. Change commands under Preferences, Input Gestures, NVDA Extra Settings. Record-specific Quick find shortcuts are assigned in the record editor. NVDA+Shift+C can conflict with the column-header command in Word and Excel; reassign it when necessary.

## Documentation

- [English user manual](addon/doc/en/readme.html)
- [Turkish user manual](addon/doc/tr/readme.html)
- [Release history](CHANGELOG.md)
- [Development and publication instructions, in Turkish](DEVELOPMENT.md)

GitHub may display HTML as source. Read the rendered manual through NVDA’s installed add-on help, or download and open the HTML in a browser.

## Storage and translation

Bookmarks, Quick find records and backups reside in the `extraSettings` subdirectory of NVDA’s user configuration directory. Legacy files migrate automatically. NVDA manages checkbox preferences and Input Gestures assignments. Speech history and translation caches remain in memory.

Translation requires an internet connection and sends the requested line to Google. No account or API key is required. The service uses an unofficial web endpoint and may require adaptation if the service changes. Automatic translation requires both permission in settings and a session started with its shortcut.

`update.json` follows the developer’s existing release metadata format. The add-on does not read it to download its own updates. Each release must be submitted separately for Add-on Store distribution.

## License

[GNU General Public License version 2](LICENSE). The license text was taken from the developer’s Kurum Rehberi repository. Bundled NVDA test fixtures retain their original notices; see [fixture provenance](tests/fixtures/nvda-source/README.md).
