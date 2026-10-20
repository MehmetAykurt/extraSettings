"""Register the extensible Extra Settings category in NVDA settings."""

from collections.abc import Mapping

import addonHandler
import config
import core
import globalPluginHandler
import globalVars
import inputCore
import languageHandler
import wx
import ui
from gui import guiHelper
from gui.settingsDialogs import NVDASettingsDialog, SettingsPanel
from scriptHandler import script
from logHandler import log

addonHandler.initTranslation()

from .webCycle import WebCycle
from .skipBlankLines import SkipBlankLines
from .clipboardAnnouncements import ClipboardAnnouncements
from .speechHistory import SpeechHistory
from .historyBuffer import parseLimit
from .applicationsKey import ApplicationsKey, shortcutLabel
from .appendClipboard import appendSelection
from .textStatistics import TextStatistics
from .clipboardInspector import ClipboardInspector
from .bookmarks import Bookmarks
from .quickFind import QuickFind
from .numpadNavigation import NumpadNavigation
from .lineTranslation import LineTranslation
from .automaticTranslation import AutomaticTranslation
from .translationService import languageChoices
from .storage import prepareDataDirectory
from .bookmarkStore import BookmarkError

CONFIG_SECTION = "extraSettings"
CONFIG_SPEC = {
    "webCycle": "boolean(default=False)",
    "skipWebBlankLines": "boolean(default=False)",
    "announceClipboard": "boolean(default=False)",
    "recordHistory": "boolean(default=False)",
    "historyLimit": "integer(default=100, min=1, max=1000)",
    "historyOrder": "option('newestFirst', 'newestLast', default='newestFirst')",
    "replaceApplicationsKey": "boolean(default=False)",
    "applicationsGesture": "string(default='')",
    "appendClipboard": "boolean(default=False)",
    "textStatistics": "boolean(default=False)",
    "inspectClipboard": "boolean(default=False)",
    "bookmarks": "boolean(default=False)",
    "quickFind": "boolean(default=False)",
    "numpadNavigation": "boolean(default=False)",
    "lineTranslation": "boolean(default=False)",
    "manualTranslation": "boolean(default=True)",
    "automaticTranslation": "boolean(default=False)",
    "translationTarget": "string(default='tr')",
}

_speechHistoryController = None
_translationController = None
_skipBlankLinesController = None
_textStatisticsController = None
_clipboardInspectorController = None
_bookmarksController = None
_quickFindController = None


def commandShortcutLabel(scriptName):
    """Read effective mappings, including removed defaults and multiple keys."""
    def scriptInfos(value):
        if hasattr(value, "scriptName"):
            yield value
        elif isinstance(value, Mapping):
            for item in value.values():
                yield from scriptInfos(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from scriptInfos(item)

    labels = []
    for info in scriptInfos(inputCore.manager.getAllGestureMappings()):
        if info.moduleName != __name__ or info.scriptName != scriptName:
            continue
        for gesture in info.gestures:
            try:
                display = inputCore.getDisplayTextForGestureIdentifier(gesture)[1]
            except LookupError:
                display = gesture.partition(":")[2]
            display = "+".join(part.upper() if len(part) == 1 else part for part in display.split("+"))
            if display not in labels:
                labels.append(display)
    return ", ".join(labels) if labels else _("No shortcut assigned")


def historyShortcutLabel():
    return commandShortcutLabel("showSpeechHistory")


class ExtraSettingsPanel(SettingsPanel):
    # Translators: Category name in NVDA's Settings dialog.
    title = _("Extra Settings")

    def makeSettings(self, settingsSizer):
        helper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
        # Translators: Checkbox to wrap quick navigation in web documents.
        self.webCycle = helper.addItem(wx.CheckBox(
            self, label=_("Enable cycling on web pages"),
        ))
        self.webCycle.SetValue(config.conf[CONFIG_SECTION]["webCycle"])
        self.skipWebBlankLines = helper.addItem(wx.CheckBox(
            self, label=_("Skip blank lines on web pages")))
        self.skipWebBlankLines.SetValue(config.conf[CONFIG_SECTION]["skipWebBlankLines"])
        # Translators: Second checkbox in Extra Settings.
        self.announceClipboard = helper.addItem(wx.CheckBox(
            self, label=_("Announce cut, copy and paste operations"),
        ))
        self.announceClipboard.SetValue(config.conf[CONFIG_SECTION]["announceClipboard"])
        self.recordHistory = helper.addItem(wx.CheckBox(self, label=self._historyLabel()))
        self.recordHistory.SetValue(config.conf[CONFIG_SECTION]["recordHistory"])
        self.recordHistory.Bind(wx.EVT_CHECKBOX, self.onHistoryChanged)
        self.recordHistory.Bind(wx.EVT_SET_FOCUS, self.onHistoryFocus)
        self.historyOptions = helper.addItem(wx.Panel(self))
        optionsSizer = wx.BoxSizer(wx.VERTICAL)
        optionsHelper = guiHelper.BoxSizerHelper(self.historyOptions, sizer=optionsSizer)
        self.historyLimit = optionsHelper.addLabeledControl(
            _("Line count"), wx.TextCtrl,
            value=str(config.conf[CONFIG_SECTION]["historyLimit"]),
        )
        self.historyOrder = optionsHelper.addLabeledControl(
            _("Display order"), wx.Choice,
            # These names follow the user's explicitly defined ordering.
            choices=[_("Beginning to end"), _("End to beginning")],
        )
        self.historyOrder.SetSelection(0 if config.conf[CONFIG_SECTION]["historyOrder"] == "newestFirst" else 1)
        self.historyOptions.SetSizer(optionsSizer)
        self.historyOptions.Show(self.recordHistory.GetValue())
        self.replaceApplicationsKey = helper.addItem(wx.CheckBox(
            self, label=_("Use instead of the Applications key"),
        ))
        self.replaceApplicationsKey.SetValue(config.conf[CONFIG_SECTION]["replaceApplicationsKey"])
        self.replaceApplicationsKey.Bind(wx.EVT_CHECKBOX, self.onApplicationsChanged)
        self.applicationsGesture = config.conf[CONFIG_SECTION]["applicationsGesture"]
        self.setApplicationsKey = helper.addItem(wx.Button(self, label=self._applicationsButtonLabel()))
        self.setApplicationsKey.Bind(wx.EVT_BUTTON, self.onSetApplicationsKey)
        self.setApplicationsKey.Show(self.replaceApplicationsKey.GetValue())
        self.appendClipboard = helper.addItem(wx.CheckBox(self, label=self._appendLabel()))
        self.appendClipboard.SetValue(config.conf[CONFIG_SECTION]["appendClipboard"])
        self.appendClipboard.Bind(wx.EVT_SET_FOCUS, self.onAppendFocus)
        self.textStatistics = helper.addItem(wx.CheckBox(self, label=self._statisticsLabel()))
        self.textStatistics.SetValue(config.conf[CONFIG_SECTION]["textStatistics"])
        self.textStatistics.Bind(wx.EVT_SET_FOCUS, self.onStatisticsFocus)
        self.inspectClipboard = helper.addItem(wx.CheckBox(self, label=self._clipboardInspectorLabel()))
        self.inspectClipboard.SetValue(config.conf[CONFIG_SECTION]["inspectClipboard"])
        self.inspectClipboard.Bind(wx.EVT_SET_FOCUS, self.onClipboardInspectorFocus)
        self.bookmarks = helper.addItem(wx.CheckBox(self, label=self._bookmarksLabel()))
        self.bookmarks.SetValue(config.conf[CONFIG_SECTION]["bookmarks"])
        self.bookmarks.Bind(wx.EVT_SET_FOCUS, self.onBookmarksFocus)
        self.quickFind = helper.addItem(wx.CheckBox(self, label=self._quickFindLabel()))
        self.quickFind.SetValue(config.conf[CONFIG_SECTION]["quickFind"])
        self.quickFind.Bind(wx.EVT_SET_FOCUS, self.onQuickFindFocus)
        # Translators: Use the eight keypad navigation keys with Num Lock off.
        self.numpadNavigation = helper.addItem(wx.CheckBox(
            self, label=_("Use the numeric keypad as navigation keys"),
        ))
        self.numpadNavigation.SetValue(config.conf[CONFIG_SECTION]["numpadNavigation"])
        self.translation = helper.addItem(wx.CheckBox(self, label=_("Translation")))
        self.translation.SetValue(config.conf[CONFIG_SECTION]["lineTranslation"])
        self.translation.Bind(wx.EVT_CHECKBOX, self.onTranslationChanged)
        self.translationOptions = helper.addItem(wx.Panel(self))
        translationSizer = wx.BoxSizer(wx.VERTICAL)
        translationHelper = guiHelper.BoxSizerHelper(self.translationOptions, sizer=translationSizer)
        choices = languageChoices(languageHandler.getLanguage())
        self._targetCodes = [code for code, label in choices]
        self.translationTarget = translationHelper.addLabeledControl(
            _("Target language"), wx.Choice, choices=[label for code, label in choices],
        )
        target = config.conf[CONFIG_SECTION]["translationTarget"]
        self.translationTarget.SetSelection(self._targetCodes.index(target if target in self._targetCodes else "tr"))
        self.lineTranslation = translationHelper.addItem(wx.CheckBox(
            self.translationOptions, label=self._translationLabel()))
        self.lineTranslation.SetValue(config.conf[CONFIG_SECTION]["manualTranslation"])
        self.lineTranslation.Bind(wx.EVT_SET_FOCUS, self.onTranslationFocus)
        self.automaticTranslation = translationHelper.addItem(wx.CheckBox(
            self.translationOptions, label=self._automaticTranslationLabel()))
        self.automaticTranslation.SetValue(config.conf[CONFIG_SECTION]["automaticTranslation"])
        self.automaticTranslation.Bind(wx.EVT_SET_FOCUS, self.onAutomaticTranslationFocus)
        self.translationOptions.SetSizer(translationSizer)
        self.translationOptions.Show(self.translation.GetValue())

    def _translationLabel(self):
        return _("Line translation — {shortcut}").format(shortcut=commandShortcutLabel("translateCurrentLine"))

    def _automaticTranslationLabel(self):
        return _("Automatic translation — {shortcut}").format(shortcut=commandShortcutLabel("toggleAutomaticTranslation"))

    def onAutomaticTranslationFocus(self, event):
        self.automaticTranslation.SetLabel(self._automaticTranslationLabel())
        event.Skip()

    def onTranslationFocus(self, event):
        self.lineTranslation.SetLabel(self._translationLabel())
        self.automaticTranslation.SetLabel(self._automaticTranslationLabel())
        event.Skip()

    def onTranslationChanged(self, event):
        self.translationOptions.Show(self.translation.GetValue())
        self.Layout()
        self._sendLayoutUpdatedEvent()

    def _appendLabel(self):
        return _("Append to clipboard — {shortcut}").format(shortcut=commandShortcutLabel("appendToClipboard"))

    def _statisticsLabel(self):
        return _("Announce word and character counts — {shortcut}").format(shortcut=commandShortcutLabel("showTextStatistics"))

    def onStatisticsFocus(self, event):
        self.textStatistics.SetLabel(self._statisticsLabel())
        event.Skip()

    def _clipboardInspectorLabel(self):
        return _("Inspect clipboard — {shortcut}").format(shortcut=commandShortcutLabel("inspectClipboard"))

    def onClipboardInspectorFocus(self, event):
        self.inspectClipboard.SetLabel(self._clipboardInspectorLabel())
        event.Skip()

    def _quickFindLabel(self):
        return _("Quick find — {shortcut}").format(shortcut=commandShortcutLabel("showQuickFind"))

    def onQuickFindFocus(self, event):
        self.quickFind.SetLabel(self._quickFindLabel())
        event.Skip()

    def _bookmarksLabel(self):
        return _("Bookmarks — {shortcut}").format(shortcut=commandShortcutLabel("showBookmarks"))

    def onBookmarksFocus(self, event):
        self.bookmarks.SetLabel(self._bookmarksLabel())
        event.Skip()

    def onAppendFocus(self, event):
        self.appendClipboard.SetLabel(self._appendLabel())
        event.Skip()

    def _applicationsButtonLabel(self):
        if not self.applicationsGesture:
            return _("Set key")
        return _("Set key — {shortcut}").format(shortcut=shortcutLabel(self.applicationsGesture))

    def onApplicationsChanged(self, event):
        self.setApplicationsKey.Show(self.replaceApplicationsKey.GetValue())
        self.Layout()
        self._sendLayoutUpdatedEvent()

    def onSetApplicationsKey(self, event):
        from .keyCapture import KeyCaptureDialog
        dialog = KeyCaptureDialog(self)
        try:
            if not dialog.beginCapture():
                ui.message(_("Another key capture is already in progress."))
                return
            if dialog.ShowModal() == wx.ID_OK and dialog.identifier:
                self.applicationsGesture = dialog.identifier
                self.setApplicationsKey.SetLabel(self._applicationsButtonLabel())
                self.Layout()
                self._sendLayoutUpdatedEvent()
                ui.message(_("Selected shortcut: {shortcut}. Choose OK or Apply to activate it.").format(
                    shortcut=shortcutLabel(self.applicationsGesture),
                ))
            else:
                ui.message(_("Key selection canceled."))
        finally:
            dialog.stopCapture()
            dialog.Destroy()

    def _historyLabel(self):
        return _("Enable speech history — {shortcut}").format(shortcut=historyShortcutLabel())

    def onHistoryFocus(self, event):
        self.recordHistory.SetLabel(self._historyLabel())
        event.Skip()

    def onPanelActivated(self):
        self.recordHistory.SetLabel(self._historyLabel())
        self.appendClipboard.SetLabel(self._appendLabel())
        self.textStatistics.SetLabel(self._statisticsLabel())
        self.inspectClipboard.SetLabel(self._clipboardInspectorLabel())
        self.bookmarks.SetLabel(self._bookmarksLabel())
        self.quickFind.SetLabel(self._quickFindLabel())
        self.lineTranslation.SetLabel(self._translationLabel())
        self.automaticTranslation.SetLabel(self._automaticTranslationLabel())
        super().onPanelActivated()

    def onHistoryChanged(self, event):
        self.historyOptions.Show(self.recordHistory.GetValue())
        self.Layout()
        self._sendLayoutUpdatedEvent()

    def isValid(self):
        if not 0 <= self.translationTarget.GetSelection() < len(self._targetCodes):
            self._validationErrorMessageBox(_("Choose a target language."), _("Target language"))
            self.translationTarget.SetFocus()
            return False
        if self.replaceApplicationsKey.GetValue() and not self.applicationsGesture:
            self._validationErrorMessageBox(_("Choose a shortcut using Set key first."), _("Set key"))
            self.setApplicationsKey.SetFocus()
            return False
        if self.recordHistory.GetValue():
            try:
                parseLimit(self.historyLimit.GetValue())
            except ValueError:
                self._validationErrorMessageBox(
                    _("Enter a whole number from 1 to 1000."), _("Line count"),
                )
                self.historyLimit.SetFocus()
                self.historyLimit.SelectAll()
                return False
        return True

    def onSave(self):
        enabled = self.recordHistory.GetValue()
        if self.replaceApplicationsKey.GetValue() and not self.applicationsGesture:
            raise ValueError("Applications key shortcut is required")
        targetIndex = self.translationTarget.GetSelection()
        if not 0 <= targetIndex < len(self._targetCodes):
            raise ValueError("Target language is required")
        # Validate before modifying any settings, including the older options.
        limit = parseLimit(self.historyLimit.GetValue()) if enabled else config.conf[CONFIG_SECTION]["historyLimit"]
        config.conf[CONFIG_SECTION]["webCycle"] = self.webCycle.GetValue()
        config.conf[CONFIG_SECTION]["skipWebBlankLines"] = self.skipWebBlankLines.GetValue()
        config.conf[CONFIG_SECTION]["announceClipboard"] = self.announceClipboard.GetValue()
        config.conf[CONFIG_SECTION]["recordHistory"] = enabled
        config.conf[CONFIG_SECTION]["historyLimit"] = limit
        config.conf[CONFIG_SECTION]["historyOrder"] = "newestFirst" if self.historyOrder.GetSelection() == 0 else "newestLast"
        config.conf[CONFIG_SECTION]["replaceApplicationsKey"] = self.replaceApplicationsKey.GetValue()
        config.conf[CONFIG_SECTION]["applicationsGesture"] = self.applicationsGesture
        config.conf[CONFIG_SECTION]["appendClipboard"] = self.appendClipboard.GetValue()
        config.conf[CONFIG_SECTION]["textStatistics"] = self.textStatistics.GetValue()
        config.conf[CONFIG_SECTION]["inspectClipboard"] = self.inspectClipboard.GetValue()
        config.conf[CONFIG_SECTION]["quickFind"] = self.quickFind.GetValue()
        config.conf[CONFIG_SECTION]["bookmarks"] = self.bookmarks.GetValue()
        config.conf[CONFIG_SECTION]["numpadNavigation"] = self.numpadNavigation.GetValue()
        config.conf[CONFIG_SECTION]["lineTranslation"] = self.translation.GetValue()
        config.conf[CONFIG_SECTION]["manualTranslation"] = self.lineTranslation.GetValue()
        config.conf[CONFIG_SECTION]["automaticTranslation"] = self.automaticTranslation.GetValue()
        config.conf[CONFIG_SECTION]["translationTarget"] = self._targetCodes[targetIndex]
        if _speechHistoryController is not None:
            _speechHistoryController.applySettings()
        if _translationController is not None:
            _translationController.applySettings()
        if _skipBlankLinesController is not None:
            _skipBlankLinesController.applySettings()
        if _textStatisticsController is not None:
            _textStatisticsController.applySettings()
        if _clipboardInspectorController is not None:
            _clipboardInspectorController.applySettings()
        if _bookmarksController is not None:
            _bookmarksController.applySettings()
        if _quickFindController is not None:
            _quickFindController.applySettings()


def _movePanelToEnd():
    categories = NVDASettingsDialog.categoryClasses
    while ExtraSettingsPanel in categories:
        categories.remove(ExtraSettingsPanel)
    categories.append(ExtraSettingsPanel)


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    """Load automatically with no separate window or activation shortcut."""

    def __init__(self, *args, **kwargs):
        global _speechHistoryController, _translationController, _skipBlankLinesController, _textStatisticsController, _clipboardInspectorController, _bookmarksController, _quickFindController
        super().__init__(*args, **kwargs)
        config.conf.spec[CONFIG_SECTION] = CONFIG_SPEC
        try:
            prepareDataDirectory(globalVars.appArgs.configPath)
        except BookmarkError:
            # A storage failure must not prevent NVDA settings from opening.
            # Feature dialogs retry migration and display their write warning.
            log.error("ExtraSettings user data directory could not be prepared")
        self._speechHistory = SpeechHistory(lambda: config.conf[CONFIG_SECTION])
        _speechHistoryController = self._speechHistory
        self._webCycle = WebCycle(lambda: config.conf[CONFIG_SECTION]["webCycle"])
        self._skipBlankLines = SkipBlankLines(lambda: config.conf[CONFIG_SECTION]["skipWebBlankLines"])
        _skipBlankLinesController = self._skipBlankLines
        self._textStatistics = TextStatistics(lambda: config.conf[CONFIG_SECTION]["textStatistics"])
        _textStatisticsController = self._textStatistics
        self._clipboardInspector = ClipboardInspector(lambda: config.conf[CONFIG_SECTION]["inspectClipboard"])
        _clipboardInspectorController = self._clipboardInspector
        self._quickFind = QuickFind(lambda: config.conf[CONFIG_SECTION]["quickFind"])
        _quickFindController = self._quickFind
        self._bookmarks = Bookmarks(lambda: config.conf[CONFIG_SECTION]["bookmarks"])
        _bookmarksController = self._bookmarks
        self._applicationsKey = ApplicationsKey(lambda: config.conf[CONFIG_SECTION])
        self._numpadNavigation = NumpadNavigation(lambda: config.conf[CONFIG_SECTION]["numpadNavigation"])
        self._lineTranslation = LineTranslation(lambda: config.conf[CONFIG_SECTION])
        self._lineTranslation.automatic = AutomaticTranslation(lambda: config.conf[CONFIG_SECTION])
        _translationController = self._lineTranslation
        self._clipboardAnnouncements = ClipboardAnnouncements(
            lambda: config.conf[CONFIG_SECTION]["announceClipboard"] and not self._speechHistory.isViewing,
            shouldIgnore=self._ignoreClipboardAnnouncement,
        )
        self._active = True
        _movePanelToEnd()
        # Finish ordering after all startup plug-ins have registered. Never
        # wrap NVDA's dialog constructor or reorder an already open dialog.
        core.postNvdaStartup.register(self._onStartupComplete)
        core.callLater(0, self._onStartupComplete)

    def _onStartupComplete(self):
        if not self._active:
            return
        # Script reload can occur with a settings dialog open. Reordering its
        # categories would invalidate the dialog's existing category indexes.
        if any(isinstance(dialog, NVDASettingsDialog) for dialog in NVDASettingsDialog._instances):
            return
        _movePanelToEnd()

    @script(
        description=_("Show speech history"),
        category=_("NVDA Extra Settings"),
        gesture="kb:NVDA+g",
        speakOnDemand=True,
    )
    def script_showSpeechHistory(self, gesture):
        # The shortcut opens a viewer only when invoked by the user.
        self._speechHistory.show()

    @script(
        description=_("Translate the current line"),
        category=_("NVDA Extra Settings"),
        gesture="kb:NVDA+shift+t",
        speakOnDemand=True,
    )
    def script_translateCurrentLine(self, gesture):
        self._lineTranslation.translateCurrentLine()

    @script(
        description=_("Automatic translation"),
        category=_("NVDA Extra Settings"),
        gesture="kb:NVDA+shift+o",
        speakOnDemand=True,
    )
    def script_toggleAutomaticTranslation(self, gesture):
        self._lineTranslation.automatic.toggle()

    @script(
        description=_("Append to clipboard"),
        category=_("NVDA Extra Settings"),
        gesture="kb:windows+c",
        speakOnDemand=True,
    )
    def script_appendToClipboard(self, gesture):
        if not config.conf[CONFIG_SECTION]["appendClipboard"]:
            gesture.send()
            return
        appendSelection()

    @script(
        description=_("Announce word and character counts"),
        category=_("NVDA Extra Settings"),
        gesture="kb:NVDA+shift+k",
        speakOnDemand=True,
    )
    def script_showTextStatistics(self, gesture):
        self._textStatistics.show()

    @script(
        description=_("Inspect clipboard"),
        category=_("NVDA Extra Settings"),
        gesture="kb:NVDA+shift+c",
        speakOnDemand=True,
    )
    def script_inspectClipboard(self, gesture):
        self._clipboardInspector.show()

    @script(
        description=_("Bookmarks"),
        category=_("NVDA Extra Settings"),
        gesture="kb:NVDA+shift+y",
        speakOnDemand=True,
    )
    def script_showBookmarks(self, gesture):
        self._bookmarks.show()

    @script(
        description=_("Quick find"),
        category=_("NVDA Extra Settings"),
        gesture="kb:NVDA+shift+h",
        speakOnDemand=True,
    )
    def script_showQuickFind(self, gesture):
        self._quickFind.show()

    def _ignoreClipboardAnnouncement(self, gesture):
        if self._applicationsKey.matches(gesture):
            return True
        return bool(config.conf[CONFIG_SECTION]["appendClipboard"] and
                    getattr(getattr(gesture, "script", None), "__func__", None)
                    is type(self).script_appendToClipboard)

    def getScript(self, gesture):
        if hasattr(self, "_applicationsKey") and self._applicationsKey.matches(gesture):
            return self.script_applicationsKey
        if hasattr(self, "_numpadNavigation"):
            navigationScript = self._numpadNavigation.getScript(gesture)
            if navigationScript is not None:
                return navigationScript
        existing = super().getScript(gesture)
        if existing is not None:
            return existing
        if hasattr(self, "_quickFind"):
            return self._quickFind.getScript(gesture)
        return None

    def script_applicationsKey(self, gesture):
        self._applicationsKey.execute(gesture)

    script_applicationsKey.__doc__ = _("Open the context menu")

    def terminate(self):
        global _speechHistoryController, _translationController, _skipBlankLinesController, _textStatisticsController, _clipboardInspectorController, _bookmarksController, _quickFindController
        self._active = False
        core.postNvdaStartup.unregister(self._onStartupComplete)
        self._numpadNavigation.terminate()
        self._lineTranslation.terminate()
        self._applicationsKey.terminate()
        self._clipboardAnnouncements.terminate()
        self._webCycle.terminate()
        self._skipBlankLines.terminate()
        self._textStatistics.terminate()
        self._clipboardInspector.terminate()
        self._bookmarks.terminate()
        self._quickFind.terminate()
        self._speechHistory.terminate()
        if _speechHistoryController is self._speechHistory:
            _speechHistoryController = None
        if _translationController is self._lineTranslation:
            _translationController = None
        if _skipBlankLinesController is self._skipBlankLines:
            _skipBlankLinesController = None
        if _textStatisticsController is self._textStatistics:
            _textStatisticsController = None
        if _clipboardInspectorController is self._clipboardInspector:
            _clipboardInspectorController = None
        if _bookmarksController is self._bookmarks:
            _bookmarksController = None
        if _quickFindController is self._quickFind:
            _quickFindController = None
        categories = NVDASettingsDialog.categoryClasses
        while ExtraSettingsPanel in categories:
            categories.remove(ExtraSettingsPanel)
        super().terminate()
