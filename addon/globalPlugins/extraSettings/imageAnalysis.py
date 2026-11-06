"""Analyze images and extract text using Google Gemini Vision API."""
import base64
import html
import io
import json
import os
import re
import ssl
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import addonHandler
import api
import core
import globalVars
import gui
import tones
import ui
import winUser
import wx
import wx.html2
from logHandler import log

addonHandler.initTranslation()

SYSTEM_PROMPT = """Sen görme engelli bireyler için profesyonel bir görsel betimleme ve metin aktarım (OCR) uzmanısın.
Gönderilen görseli en yüksek erişilebilirlikle, tarafsız ve net bir dille incele. Yanıtını tam olarak şu iki başlık altında Türkçe olarak ver:

Analiz Raporu
- Görselde görünenleri (kişiler, nesneler, eylemler, renkler, ortam, konumlar ve genel atmosfer) görme engelli birinin zihninde tam olarak canlanmasını sağlayacak şekilde zengin, canlı ve duru bir dille açıkla.
- Yalnızca görselde gerçekten var olan ve görünen unsurları anlat. Olmayan şeyleri (örneğin "şu nesne yoktur, imza yoktur" gibi) kesinlikle sıralama; olumsuzlama cümleleri kurma.
- Eğer görsel resmi bir evrak, ekran görüntüsü veya grafik ise, türünü ve genel yapısını kısaca açıkla.

Görseldeki Metinler
- Görselde görülen BÜTÜN METİNLERİ yukarıdan aşağıya, soldan sağa sırayla hiçbir kelimeyi, sayıyı veya alanı atlamadan EKSİKSİZ ve KELİMESİ KELİMESİNE AYNEN AKTAR.
- Belge, fiş, fatura veya tablolardaki tüm satırları, etiketleri ve karşılık gelen verileri aynen yaz.
- Silik veya şüpheli kısımlar varsa yanına "[Not: Silik okunmuş olabilir]" yaz.
- Eğer görselde kesinlikle hiçbir yazı yoksa sadece "Görselde herhangi bir metin bulunmuyor." yaz.

ÖNEMLİ KURALLAR:
1. Başlıklarda veya metinlerde kare (#), diyez veya yıldız (*) gibi işaretler KESİNLİKLE KULLANMA. Başlıkları doğrudan sade metin olarak yaz.
2. Kesinlikle doğruluk oranı, güven puanı veya yüzde ifadesi ekleme.
"""

IMAGE_EXTENSIONS = frozenset(
    [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif"]
)
MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}
API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
MODELS = ["gemini-2.5-flash", "gemini-3.5-flash", "gemini-flash-latest", "gemini-flash-lite-latest"]
NETWORK_TIMEOUT = 35


class _BeepProgress:
    """Plays periodic soft beeps in a background thread while processing."""

    def __init__(self, interval=1.5, pitch=600, duration=40):
        self._interval = interval
        self._pitch = pitch
        self._duration = duration
        self._stopEvent = threading.Event()
        self._thread = None

    def start(self):
        self._stopEvent.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stopEvent.wait(timeout=self._interval):
            try:
                tones.beep(self._pitch, self._duration)
            except Exception:
                break

    def stop(self):
        self._stopEvent.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.2)


def _playSuccessChime():
    """Play a 3-tone ascending chime when analysis completes successfully."""
    try:
        tones.beep(523, 50)  # C5
        tones.beep(659, 50)  # E5
        tones.beep(784, 80)  # G5
    except Exception:
        pass


def _playErrorChime():
    """Play a low alert tone when analysis fails."""
    try:
        tones.beep(300, 70)
        tones.beep(220, 100)
    except Exception:
        pass


def _openRequest(request):
    """Open URL request with tolerant SSL certificate handling for Windows roots."""
    try:
        return urlopen(request, timeout=NETWORK_TIMEOUT)
    except URLError as error:
        reason = getattr(error, "reason", None)
        if not isinstance(reason, ssl.SSLCertVerificationError) or reason.verify_code != 85:
            raise
        context = ssl.create_default_context()
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        return urlopen(request, timeout=NETWORK_TIMEOUT, context=context)


def _prepareImage(rawData, originalMime):
    """Normalize and optimize image data for Gemini Vision API.

    Converts unsupported formats (TIFF, BMP, GIF) to JPEG,
    scales down images larger than 2400 pixels on any edge,
    and ensures fast upload without quality loss.
    """
    try:
        wx.InitAllImageHandlers()
        stream = io.BytesIO(rawData)
        img = wx.Image(stream)
        if not img.IsOk():
            log.warning("ImageAnalysis: wx.Image could not parse raw data; using original.")
            return rawData, originalMime

        w = img.GetWidth()
        h = img.GetHeight()
        maxEdge = 2400
        if w > maxEdge or h > maxEdge:
            if w >= h:
                newW = maxEdge
                newH = max(1, int(h * (maxEdge / w)))
            else:
                newH = maxEdge
                newW = max(1, int(w * (maxEdge / h)))
            img = img.Rescale(newW, newH, quality=wx.IMAGE_QUALITY_HIGH)

        mustConvertToJpeg = (
            originalMime in ("image/tiff", "image/bmp", "image/gif")
            or originalMime not in ("image/jpeg", "image/png", "image/webp")
            or len(rawData) > 1024 * 1024
        )
        if mustConvertToJpeg:
            outStream = io.BytesIO()
            img.SetOption("quality", 88)
            if img.SaveFile(outStream, wx.BITMAP_TYPE_JPEG):
                converted = outStream.getvalue()
                if converted:
                    log.info(
                        f"ImageAnalysis: Optimized image from {len(rawData)} bytes ({originalMime}) "
                        f"to {len(converted)} bytes (image/jpeg) at {img.GetWidth()}x{img.GetHeight()}"
                    )
                    return converted, "image/jpeg"
    except Exception as e:
        log.error(f"ImageAnalysis: Error preparing image: {e}")
    return rawData, originalMime


def _getImageFromFile(filePath):
    """Read image bytes and mime type from a valid file path."""
    if not filePath or not os.path.isfile(filePath):
        return None, None
    ext = os.path.splitext(filePath)[1].lower()
    mime = MIME_MAP.get(ext)
    if not mime:
        return None, None
    try:
        with open(filePath, "rb") as f:
            data = f.read()
        if data:
            return data, mime
    except Exception as e:
        log.error(f"ImageAnalysis: Error reading file {filePath}: {e}")
    return None, None


def _isDesktop(focus):
    """Check if focus is on the Windows Desktop."""
    if focus is None:
        return False
    try:
        className = getattr(focus, "windowClassName", "") or ""
        if className in ("Progman", "WorkerW"):
            return True
        root = getattr(focus, "rootNVDAObject", None)
        if root is not None and getattr(root, "windowClassName", "") in ("Progman", "WorkerW"):
            return True
        parent = getattr(focus, "parent", None)
        while parent is not None:
            if getattr(parent, "windowClassName", "") in ("Progman", "WorkerW"):
                return True
            parent = getattr(parent, "parent", None)
    except Exception:
        pass
    return False


def _getExplorerHwnds(focus):
    """Collect possible HWNDs representing the active File Explorer window."""
    hwnds = set()
    if focus is None:
        return hwnds
    try:
        import ctypes
        user32 = ctypes.windll.user32
        fg = user32.GetForegroundWindow()
        if fg:
            hwnds.add(fg)
            hwnds.add(user32.GetAncestor(fg, 2))  # GA_ROOT
        h = getattr(focus, "windowHandle", 0)
        if h:
            hwnds.add(h)
            hwnds.add(user32.GetAncestor(h, 1))  # GA_PARENT
            hwnds.add(user32.GetAncestor(h, 2))  # GA_ROOT
            hwnds.add(user32.GetAncestor(h, 3))  # GA_ROOTOWNER
        root = getattr(focus, "rootNVDAObject", None)
        if root:
            rh = getattr(root, "windowHandle", 0)
            if rh:
                hwnds.add(rh)
                hwnds.add(user32.GetAncestor(rh, 2))
    except Exception:
        pass
    hwnds.discard(0)
    return hwnds


def _getExplorerFile(focus):
    """Try to obtain image file path strictly from the ACTIVE File Explorer window or Desktop."""
    if focus is None:
        return None, None, None

    app = getattr(focus, "appModule", None)
    appName = getattr(app, "appName", "").lower() if app else ""
    if appName != "explorer":
        # Focus is NOT in Explorer or Desktop (e.g. Word, Chrome, Telegram, Notepad)
        return None, None, None

    # Case A: Desktop item
    if _isDesktop(focus):
        try:
            name = getattr(focus, "name", "")
            if name:
                for base in (
                    os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"),
                    os.path.join(os.environ.get("PUBLIC", ""), "Desktop"),
                    os.path.join(os.environ.get("USERPROFILE", ""), "OneDrive", "Desktop"),
                ):
                    candidate = os.path.join(base, name)
                    data, mime = _getImageFromFile(candidate)
                    if data:
                        return data, mime, candidate
        except Exception:
            pass
        return None, None, None

    # Case B: Active File Explorer folder window (CabinetWClass)
    targetHwnds = _getExplorerHwnds(focus)
    if not targetHwnds:
        return None, None, None

    try:
        import comtypes.client
        from comtypes import CoInitialize, CoUninitialize

        CoInitialize()
        try:
            shell = comtypes.client.CreateObject("Shell.Application", dynamic=True)
            for window in shell.Windows():
                try:
                    wHwnd = int(window.HWND)
                except Exception:
                    continue
                # Strictly ignore background, minimized or unrelated Explorer windows!
                if wHwnd not in targetHwnds:
                    continue

                doc = getattr(window, "Document", None)
                if doc is None:
                    continue

                # Check focused item first
                focusItem = getattr(doc, "FocusedItem", None)
                if focusItem is not None:
                    try:
                        p = focusItem.Path
                        data, mime = _getImageFromFile(p)
                        if data:
                            return data, mime, p
                    except Exception:
                        pass

                # Check selected items in the active window
                sel = getattr(doc, "SelectedItems", None)
                if callable(sel):
                    sel = sel()
                if sel is not None and sel.Count > 0:
                    for i in range(sel.Count):
                        p = sel.Item(i).Path
                        data, mime = _getImageFromFile(p)
                        if data:
                            return data, mime, p
        finally:
            CoUninitialize()
    except Exception as e:
        log.debug(f"ImageAnalysis: Shell COM check error: {e}")

    return None, None, None


def _getClipboardImage():
    """Extract image from Windows clipboard (bitmap, copied file, or image file path)."""
    try:
        wx.InitAllImageHandlers()
        if not wx.TheClipboard.IsOpened():
            opened = wx.TheClipboard.Open()
        else:
            opened = True
        if opened:
            try:
                # 1. Direct bitmap image in clipboard (screenshots, "Copy Image" from browser/Telegram)
                if wx.TheClipboard.IsSupported(wx.DataFormat(wx.DF_BITMAP)):
                    bdo = wx.BitmapDataObject()
                    if wx.TheClipboard.GetData(bdo):
                        bmp = bdo.GetBitmap()
                        if bmp and bmp.IsOk():
                            img = bmp.ConvertToImage()
                            if img.IsOk():
                                maxEdge = 2400
                                w = img.GetWidth()
                                h = img.GetHeight()
                                if w > maxEdge or h > maxEdge:
                                    if w >= h:
                                        newW = maxEdge
                                        newH = max(1, int(h * (maxEdge / w)))
                                    else:
                                        newH = maxEdge
                                        newW = max(1, int(w * (maxEdge / h)))
                                    img = img.Rescale(newW, newH, quality=wx.IMAGE_QUALITY_HIGH)

                                stream = io.BytesIO()
                                img.SetOption("quality", 88)
                                if img.SaveFile(stream, wx.BITMAP_TYPE_JPEG):
                                    data = stream.getvalue()
                                    if data:
                                        log.info("ImageAnalysis: Retrieved bitmap image from clipboard")
                                        return data, "image/jpeg"

                # 2. Files copied to clipboard via Ctrl+C in File Explorer or Desktop
                if wx.TheClipboard.IsSupported(wx.DataFormat(wx.DF_FILENAME)):
                    fdo = wx.FileDataObject()
                    if wx.TheClipboard.GetData(fdo):
                        for filePath in fdo.GetFilenames():
                            data, mime = _getImageFromFile(filePath)
                            if data:
                                log.info(f"ImageAnalysis: Retrieved image file from clipboard: {filePath}")
                                return data, mime

                # 3. File path string copied to clipboard as text
                for textFormat in (wx.DF_UNICODETEXT, wx.DF_TEXT):
                    if wx.TheClipboard.IsSupported(wx.DataFormat(textFormat)):
                        tdo = wx.TextDataObject()
                        if wx.TheClipboard.GetData(tdo):
                            textVal = (tdo.GetText() or "").strip().strip('"').strip("'")
                            if textVal and os.path.isfile(textVal):
                                data, mime = _getImageFromFile(textVal)
                                if data:
                                    log.info(f"ImageAnalysis: Retrieved image path from clipboard text: {textVal}")
                                    return data, mime
                        break
            finally:
                wx.TheClipboard.Close()
    except Exception as e:
        log.error(f"ImageAnalysis: Clipboard image capture failed: {e}")
    return None, None


def _captureWindowViaPrintWindow(hwnd):
    """Capture a window via user32.PrintWindow PW_RENDERFULLCONTENT."""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None, None
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w < 50 or h < 50:
            return None, None
        hdcScreen = user32.GetDC(0)
        hdcMem = gdi32.CreateCompatibleDC(hdcScreen)
        hbm = gdi32.CreateCompatibleBitmap(hdcScreen, w, h)
        oldBmp = gdi32.SelectObject(hdcMem, hbm)

        # PW_RENDERFULLCONTENT = 2
        user32.PrintWindow(hwnd, hdcMem, 2)

        bmp = wx.Bitmap()
        bmp.CreateFromHBITMAP(hbm)

        gdi32.SelectObject(hdcMem, oldBmp)
        gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(hdcMem)
        user32.ReleaseDC(0, hdcScreen)

        if bmp.IsOk():
            img = bmp.ConvertToImage()
            if img.IsOk():
                maxEdge = 2400
                if w > maxEdge or h > maxEdge:
                    if w >= h:
                        newW = maxEdge
                        newH = max(1, int(h * (maxEdge / w)))
                    else:
                        newH = maxEdge
                        newW = max(1, int(w * (maxEdge / h)))
                    img = img.Rescale(newW, newH, quality=wx.IMAGE_QUALITY_HIGH)
                stream = io.BytesIO()
                img.SetOption("quality", 88)
                if img.SaveFile(stream, wx.BITMAP_TYPE_JPEG):
                    return stream.getvalue(), "image/jpeg"
    except Exception as e:
        log.debug(f"ImageAnalysis: PrintWindow capture failed: {e}")
    return None, None


def _captureScreenRect(left, top, width, height):
    """Capture a screen rectangle and return optimized JPEG bytes using Windows Graphics Capture."""
    try:
        if left < 0:
            width += left
            left = 0
        if top < 0:
            height += top
            top = 0
        if width < 30 or height < 30:
            return None, None

        # 1. Primary Method: NVDA's Windows Graphics Capture (_wgcCapture)
        # This properly captures modern DWM, GPU-accelerated and transparent windows on Windows 10/11
        try:
            from contentRecog import _wgcCapture, RecogImageInfo
            if _wgcCapture.isSupported():
                w = int(width)
                h = int(height)
                imgInfo = RecogImageInfo(int(left), int(top), w, h, 1.0)
                buffer = _wgcCapture.captureImage(imgInfo)
                if buffer:
                    bgra = bytes(buffer)
                    rgb = bytearray(w * h * 3)
                    rgb[0::3] = bgra[2::4]
                    rgb[1::3] = bgra[1::4]
                    rgb[2::3] = bgra[0::4]

                    wx.InitAllImageHandlers()
                    img = wx.Image(w, h, bytes(rgb))
                    if img.IsOk():
                        maxEdge = 2400
                        if w > maxEdge or h > maxEdge:
                            if w >= h:
                                newW = maxEdge
                                newH = max(1, int(h * (maxEdge / w)))
                            else:
                                newH = maxEdge
                                newW = max(1, int(w * (maxEdge / h)))
                            img = img.Rescale(newW, newH, quality=wx.IMAGE_QUALITY_HIGH)

                        stream = io.BytesIO()
                        img.SetOption("quality", 88)
                        if img.SaveFile(stream, wx.BITMAP_TYPE_JPEG):
                            data = stream.getvalue()
                            if data:
                                log.info(f"ImageAnalysis: Screen capture succeeded via WGC ({w}x{h})")
                                return data, "image/jpeg"
        except Exception as e:
            log.warning(f"ImageAnalysis: WGC screen capture error: {e}")

        # 2. Fallback Method: PrintWindow on foreground window
        try:
            hwnd = winUser.getForegroundWindow()
            if hwnd:
                data, mime = _captureWindowViaPrintWindow(hwnd)
                if data:
                    return data, mime
        except Exception as e:
            log.warning(f"ImageAnalysis: PrintWindow fallback error: {e}")

    except Exception as e:
        log.error(f"ImageAnalysis: All screen capture methods failed: {e}")
    return None, None


def _findImageToAnalyze():
    """Find image strictly from focused file in File Explorer/Desktop, or from clipboard."""
    focus = api.getFocusObject()

    # Priority 1: Focused file in File Explorer or on Desktop
    data, mime, path = _getExplorerFile(focus)
    if data:
        log.info(f"ImageAnalysis: Using file {path}")
        return data, mime, _("File: {name}").format(name=os.path.basename(path))

    # Priority 2: Clipboard image (direct bitmap, copied image file, or copied file path)
    data, mime = _getClipboardImage()
    if data:
        log.info("ImageAnalysis: Using clipboard image")
        return data, mime, _("Clipboard image")

    # Screen capture fallback completely removed as requested.
    return None, None, None


def _formatResponseHtml(rawText):
    """Convert AI response into clean, accessible HTML without markdown symbols (#, **, etc.).

    Creates true HTML headings (<h2>) so NVDA users can jump with the H key,
    completely removes '###' and '##' signs, and excludes 'Doğruluk Düzeyi'.
    """
    lines = rawText.splitlines()
    htmlParts = []
    currentSection = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if currentSection:
                currentSection.append("<br>")
            continue

        clean = stripped.lstrip("#").strip()
        cleanLower = clean.lower()

        # Section 1: Analiz Raporu
        if any(cleanLower.startswith(k) for k in ["analiz raporu", "görsel betimlemesi", "bölüm 1", "görsel analizi"]):
            if currentSection:
                htmlParts.append(f"<p>{''.join(currentSection)}</p>")
                currentSection = []
            htmlParts.append(f"<h2>{_('Analysis Report')}</h2>")
            colonIdx = clean.find(":")
            if colonIdx != -1 and len(clean[colonIdx + 1:].strip()) > 3:
                currentSection.append(html.escape(clean[colonIdx + 1:].strip()) + "<br>")
            continue

        # Section 2: Görseldeki Metinler
        if any(cleanLower.startswith(k) for k in ["görseldeki metinler", "bölüm 2", "metinler", "belgedeki metinler"]):
            if currentSection:
                htmlParts.append(f"<p>{''.join(currentSection)}</p>")
                currentSection = []
            htmlParts.append(f"<h2>{_('Text in Image')}</h2>")
            colonIdx = clean.find(":")
            if colonIdx != -1 and len(clean[colonIdx + 1:].strip()) > 3:
                currentSection.append(html.escape(clean[colonIdx + 1:].strip()) + "<br>")
            continue

        # Section 3: Doğruluk Düzeyi (EXCLUDE completely as requested)
        if any(cleanLower.startswith(k) for k in ["doğruluk düzeyi", "bölüm 3", "güven oranı", "doğruluk oranı"]):
            if currentSection:
                htmlParts.append(f"<p>{''.join(currentSection)}</p>")
                currentSection = []
            break

        formatted = re.sub(r"[\*_]{1,3}", "", clean)
        formatted = formatted.lstrip("#").strip()
        currentSection.append(html.escape(formatted) + "<br>")

    if currentSection:
        htmlParts.append(f"<p>{''.join(currentSection)}</p>")

    return "\n".join(htmlParts)


def _formatQuotaErrorMessage(errJson):
    """Distinguish between short-term rate limit (per minute) and daily quota exhaustion."""
    try:
        err = errJson.get("error", {})
        msg = err.get("message", "")
        details = err.get("details", [])

        isDaily = False
        isPerMinute = False
        retrySec = 0

        for d in details:
            if "retryDelay" in d:
                delayStr = str(d["retryDelay"]).rstrip("s")
                try:
                    retrySec = float(delayStr)
                    if retrySec > 120:
                        isDaily = True
                    elif retrySec > 0:
                        isPerMinute = True
                except Exception:
                    pass

            violations = d.get("violations", [])
            for v in violations:
                desc = (
                    v.get("quotaId", "")
                    + " "
                    + v.get("quotaMetric", "")
                    + " "
                    + v.get("description", "")
                ).lower()
                if any(k in desc for k in ["perday", "per_day", "daily", "free_tier_requests"]):
                    isDaily = True
                elif any(k in desc for k in ["perminute", "per_minute", "minute"]):
                    isPerMinute = True

        msgLower = msg.lower()
        if "per day" in msgLower or "requests per day" in msgLower:
            isDaily = True
        elif "per minute" in msgLower or "requests per minute" in msgLower:
            isPerMinute = True

        if isDaily:
            return _(
                "Google Gemini daily usage quota exceeded. Please try again tomorrow or use another Google API key."
            )
        if isPerMinute or (retrySec and retrySec <= 120):
            return _(
                "Short-term request rate limit reached. Please wait a few seconds and try again."
            )
    except Exception:
        pass

    return _("Usage quota exceeded or request limit reached. Please try again later.")


def _sendGeminiRequest(apiKey, contents, temperature=0.1, maxOutputTokens=8192):
    """Send contents to Google Gemini API with fallback across models and automatic 429/503 retry."""
    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": maxOutputTokens,
        },
    }
    body = json.dumps(payload).encode("utf-8")

    lastError = None
    lastErrorMsg = ""
    lastErrorJson = {}
    for model in MODELS:
        url = API_URL_TEMPLATE.format(model=model, key=apiKey.strip())
        req = Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "NVDA-ExtraSettings/2026",
            },
            method="POST",
        )
        for attempt in range(2):
            try:
                with _openRequest(req) as resp:
                    data = resp.read()
                    parsed = json.loads(data.decode("utf-8"))
                    candidates = parsed.get("candidates") or []
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        texts = [p.get("text", "") for p in parts if "text" in p]
                        result = "".join(texts).strip()
                        if result:
                            return result
                    raise ValueError("Boş veya anlaşılamayan yanıt alındı.")
            except HTTPError as e:
                lastError = e
                code = e.code
                errMsg = ""
                try:
                    errJson = json.loads(e.read().decode("utf-8"))
                    errMsg = errJson.get("error", {}).get("message", "")
                except Exception:
                    errMsg = str(e)

                log.error(f"ImageAnalysis: HTTP {code} on model {model} attempt {attempt}: {errMsg}")
                lastErrorMsg = errMsg or str(e)
                lastErrorJson = errJson

                # Momentary server busy (503) or rate limit per minute (429): wait 2s and retry
                if code in (429, 503) and attempt == 0:
                    import time
                    time.sleep(2)
                    continue

                if code == 400:
                    if "API_KEY_INVALID" in errMsg or "API key not valid" in errMsg or "INVALID_ARGUMENT" in errMsg:
                        raise RuntimeError(_("The entered Google Gemini API key is invalid. Please check your key."))
                    raise RuntimeError(_("API error: {msg}").format(msg=errMsg))
                elif code == 403:
                    raise RuntimeError(_("Access denied: {msg}").format(msg=errMsg))
                elif code in (404, 429, 503):
                    # Rate limit or temporary error on this model, try next model in MODELS
                    break
                else:
                    raise RuntimeError(_("Service error (HTTP {code}): {msg}").format(code=code, msg=errMsg))
            except URLError as e:
                log.error(f"ImageAnalysis: Network connection error: {e}")
                raise RuntimeError(_("Could not connect to the internet. Please check your network connection."))
            except Exception as e:
                log.error(f"ImageAnalysis: Unexpected error: {e}")
                raise RuntimeError(_("An error occurred during analysis: {error}").format(error=str(e)))

    if lastError:
        log.error(f"ImageAnalysis: All models failed. Last error: {lastErrorMsg}")
        if getattr(lastError, "code", 0) == 429:
            quotaError = _formatQuotaErrorMessage(lastErrorJson)
            raise RuntimeError(quotaError)
        raise RuntimeError(
            _("Could not reach image analysis service: {error}").format(error=lastErrorMsg)
        )


def _callGeminiVision(apiKey, imageData, mimeType):
    """Send image and prompt to Google Gemini Vision API."""
    encodedImage = base64.b64encode(imageData).decode("ascii")
    contents = [
        {
            "parts": [
                {"text": SYSTEM_PROMPT},
                {
                    "inlineData": {
                        "mimeType": mimeType,
                        "data": encodedImage,
                    }
                },
            ]
        }
    ]
    return _sendGeminiRequest(apiKey, contents, temperature=0.1, maxOutputTokens=8192)


class ImageAnalysisDialog(wx.Dialog):
    """Accessible dialog presenting the analysis report with interactive question input."""

    def __init__(self, parent, title, apiKey, imageData, mimeType, sourceDesc, initialRawText):
        super().__init__(
            parent,
            title=title,
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX | wx.MINIMIZE_BOX,
        )

        self._apiKey = apiKey
        self._imageData = imageData
        self._mimeType = mimeType
        self._sourceDesc = sourceDesc
        self._title = title
        self._isAsking = False
        self._beepProgress = None

        encodedImage = base64.b64encode(self._imageData).decode("ascii")
        self._contents = [
            {
                "role": "user",
                "parts": [
                    {"text": SYSTEM_PROMPT},
                    {
                        "inlineData": {
                            "mimeType": self._mimeType,
                            "data": encodedImage,
                        }
                    },
                ],
            },
            {
                "role": "model",
                "parts": [{"text": initialRawText}],
            },
        ]

        templatePath = os.path.join(globalVars.appDir, "message.html")
        try:
            with open(templatePath, "r", encoding="utf-8") as f:
                self._template = f.read()
        except Exception:
            self._template = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<title>{{TITLE}}</title></head><body>"
                "<div id='messageDiv'>{{MESSAGE}}</div></body></html>"
            )

        self._accumulatedHtml = _formatResponseHtml(initialRawText)
        templated = self._wrapHtml(self._accumulatedHtml)

        # Main vertical sizer
        mainSizer = wx.BoxSizer(wx.VERTICAL)

        # 1. WebView control for readable report (accessible virtual buffer)
        self._webView = wx.html2.WebView.New(self)
        self._webView.EnableContextMenu(False)
        self._webView.EnableHistory(False)
        self._webView.Bind(wx.html2.EVT_WEBVIEW_NAVIGATING, self._onNavigating)
        self._webView.SetPage(templated, "")
        self._webView.SetMinSize((600, 320))
        mainSizer.Add(self._webView, proportion=1, flag=wx.EXPAND | wx.ALL, border=8)

        # 2. Question input control
        qSizer = wx.BoxSizer(wx.VERTICAL)
        qLabel = wx.StaticText(self, label=_("&Ask a question about the image:"))
        self._questionCtrl = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        qSizer.Add(qLabel, 0, wx.BOTTOM, 4)
        qSizer.Add(self._questionCtrl, 0, wx.EXPAND)
        mainSizer.Add(qSizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=8)

        # 3. Action Buttons row
        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        self._sendButton = wx.Button(self, label=_("&Send"))
        self._copyButton = wx.Button(self, label=_("&Copy"))
        self._closeButton = wx.Button(self, wx.ID_CANCEL, label=_("Close"))

        btnSizer.Add(self._sendButton, 0, wx.RIGHT, 6)
        btnSizer.Add(self._copyButton, 0, wx.RIGHT, 6)
        btnSizer.Add(self._closeButton, 0)
        mainSizer.Add(btnSizer, 0, wx.ALIGN_RIGHT | wx.RIGHT | wx.BOTTOM, border=8)

        # Event bindings
        self._questionCtrl.Bind(wx.EVT_TEXT_ENTER, self._onSendQuestion)
        self._sendButton.Bind(wx.EVT_BUTTON, self._onSendClick)
        self._copyButton.Bind(wx.EVT_BUTTON, self._onCopyClick)
        self._closeButton.Bind(wx.EVT_BUTTON, lambda evt: self.Close())
        self.Bind(wx.EVT_CLOSE, self._onClose)

        # Accelerators for Esc, Alt+S, Alt+G, Alt+C
        accelEntries = [
            wx.AcceleratorEntry(wx.ACCEL_NORMAL, wx.WXK_ESCAPE, wx.ID_CANCEL),
            wx.AcceleratorEntry(wx.ACCEL_ALT, ord('S'), self._questionCtrl.GetId()),
            wx.AcceleratorEntry(wx.ACCEL_ALT, ord('s'), self._questionCtrl.GetId()),
            wx.AcceleratorEntry(wx.ACCEL_ALT, ord('G'), self._sendButton.GetId()),
            wx.AcceleratorEntry(wx.ACCEL_ALT, ord('g'), self._sendButton.GetId()),
            wx.AcceleratorEntry(wx.ACCEL_ALT, ord('C'), self._copyButton.GetId()),
            wx.AcceleratorEntry(wx.ACCEL_ALT, ord('c'), self._copyButton.GetId()),
        ]
        self.SetAcceleratorTable(wx.AcceleratorTable(accelEntries))

        self.SetSizer(mainSizer)
        self.SetSize((760, 560))
        self.SetMinSize((520, 380))
        self.CentreOnScreen()

    def _wrapHtml(self, bodyHtml):
        return self._template.replace("{{TITLE}}", html.escape(self._title)).replace(
            "{{MESSAGE}}", bodyHtml
        )

    def _onNavigating(self, evt):
        url = evt.GetURL()
        if url.startswith("nvda-action://"):
            evt.Veto()
            action = url[len("nvda-action://"):].strip().lower()
            if action == "close":
                self.Close()
            elif action == "copy":
                self._onCopyClick()

    def _onSendClick(self, evt=None):
        self._submitQuestion()

    def _onSendQuestion(self, evt=None):
        self._submitQuestion()

    def _onCopyClick(self, evt=None):
        try:
            text = self._webView.GetPageText()
            api.copyToClip(text)
            ui.message(_("Copied to clipboard"))
        except Exception as e:
            log.error(f"ImageAnalysis: Copy failed: {e}")

    def _submitQuestion(self):
        if self._isAsking:
            ui.message(_("A question is already being processed. Please wait."))
            return

        question = self._questionCtrl.GetValue().strip()
        if not question:
            tones.beep(300, 100)
            ui.message(_("Please enter a question."))
            return

        self._isAsking = True
        self._questionCtrl.Enable(False)
        self._sendButton.Enable(False)

        self._beepProgress = _BeepProgress(interval=1.5, pitch=600, duration=40)
        self._beepProgress.start()

        ui.message(_("Answering your question, please wait..."))

        threading.Thread(
            target=self._askWorker,
            args=(question,),
            daemon=True,
        ).start()

    def _askWorker(self, question):
        try:
            promptText = (
                "Sen görme engelli bireyler için profesyonel bir görsel danışmanısın. "
                "Görseldeki detaylara, renklere, nesnelere ve metinlere dayanarak "
                "kullanıcının şu sorusunu doğrudan, net, sade ve erişilebilir bir Türkçe ile yanıtla:\n"
                f"{question}\n\n"
                "ÖNEMLİ KURAL: Başlıklarda veya metinlerde kare (#) veya yıldız (*) gibi işaretler KULLANMA. "
                "Yalnızca görselde ve belgede görülen somut bilgilere dayanarak yanıt ver."
            )
            newTurn = {"role": "user", "parts": [{"text": promptText}]}
            tempContents = list(self._contents)
            tempContents.append(newTurn)

            answer = _sendGeminiRequest(
                self._apiKey,
                tempContents,
                temperature=0.2,
                maxOutputTokens=4096,
            )
            core.callLater(0, self._onAnswerSuccess, question, answer, tempContents)
        except Exception as e:
            core.callLater(0, self._onAnswerError, str(e))

    def _onAnswerSuccess(self, question, answerText, tempContents):
        if self._beepProgress:
            self._beepProgress.stop()
            self._beepProgress = None
        self._isAsking = False

        _playSuccessChime()

        self._contents = tempContents
        self._contents.append({"role": "model", "parts": [{"text": answerText}]})

        cleanAnswer = re.sub(r"[\*_]{1,3}", "", answerText)
        cleanAnswer = cleanAnswer.lstrip("#").strip()

        qHeadingText = _("Question: {text}").format(text=html.escape(question))
        aLines = []
        for line in cleanAnswer.splitlines():
            l = line.strip().lstrip("#").strip()
            if l:
                aLines.append(html.escape(l) + "<br>")
        aParagraph = f"<p>{''.join(aLines)}</p>"

        newTurnHtml = (
            f'<div style="margin-top: 1.5em; border-top: 1px solid #ccc; padding-top: 1em;">'
            f'<h2>{qHeadingText}</h2>\n'
            f'{aParagraph}'
            f'</div>'
        )
        self._accumulatedHtml += f"\n{newTurnHtml}"

        self._webView.SetPage(self._wrapHtml(self._accumulatedHtml), "")

        self._questionCtrl.SetValue("")
        self._questionCtrl.Enable(True)
        self._sendButton.Enable(True)
        self._questionCtrl.SetFocus()

        if len(cleanAnswer) <= 300:
            ui.message(_("Answer: {answer}").format(answer=cleanAnswer))
        else:
            ui.message(_("Answer received: {answer}").format(answer=cleanAnswer[:200] + "..."))

    def _onAnswerError(self, errorMsg):
        if self._beepProgress:
            self._beepProgress.stop()
            self._beepProgress = None
        self._isAsking = False

        _playErrorChime()
        ui.message(errorMsg)

        self._questionCtrl.Enable(True)
        self._sendButton.Enable(True)
        self._questionCtrl.SetFocus()

    def _onClose(self, evt=None):
        if self._beepProgress:
            self._beepProgress.stop()
            self._beepProgress = None
        self._isAsking = False
        self.Destroy()


class ImageAnalysis:
    """Controller for image analysis requests."""

    def __init__(self, isEnabled, getApiKey):
        self._isEnabled = isEnabled
        self._getApiKey = getApiKey
        self._isAnalyzing = False
        self._beepProgress = None

    def analyze(self):
        """Main entry point called by the keyboard shortcut."""
        if not self._isEnabled():
            ui.message(
                _(
                    "Image analysis is not enabled. Please enable it in NVDA menu / Preferences / Settings / Extra Settings."
                )
            )
            return

        apiKey = (self._getApiKey() or "").strip()
        if not apiKey:
            ui.message(
                _(
                    "Google Gemini API key has not been entered. Please set your key in NVDA Extra Settings."
                )
            )
            return

        if self._isAnalyzing:
            ui.message(_("An image is already being analyzed. Please wait."))
            return

        imageData, mimeType, sourceDesc = _findImageToAnalyze()
        if not imageData:
            ui.message(
                _(
                    "No image found to analyze. Please select an image file or copy an image to the clipboard."
                )
            )
            return

        # Prepare, resize and convert image if necessary (TIFF/BMP/large PNG -> JPEG)
        imageData, mimeType = _prepareImage(imageData, mimeType)

        self._isAnalyzing = True
        ui.message(_("Analyzing image, please wait..."))

        # Start periodic progress beeping
        self._beepProgress = _BeepProgress(interval=1.5, pitch=600, duration=40)
        self._beepProgress.start()

        threading.Thread(
            target=self._worker,
            args=(apiKey, imageData, mimeType, sourceDesc),
            daemon=True,
        ).start()

    def _worker(self, apiKey, imageData, mimeType, sourceDesc):
        try:
            result = _callGeminiVision(apiKey, imageData, mimeType)
            core.callLater(0, self._onSuccess, result, sourceDesc, apiKey, imageData, mimeType)
        except Exception as e:
            core.callLater(0, self._onError, str(e))
        finally:
            if self._beepProgress:
                self._beepProgress.stop()
                self._beepProgress = None
            self._isAnalyzing = False

    def _onSuccess(self, resultText, sourceDesc, apiKey, imageData, mimeType):
        _playSuccessChime()
        ui.message(_("Analysis completed."))
        title = _("Image Analysis: {source}").format(source=sourceDesc)

        try:
            gui.mainFrame.prePopup()
            dialog = ImageAnalysisDialog(
                parent=gui.mainFrame,
                title=title,
                apiKey=apiKey,
                imageData=imageData,
                mimeType=mimeType,
                sourceDesc=sourceDesc,
                initialRawText=resultText,
            )
            dialog.Show()
            gui.mainFrame.postPopup()
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            log.error(f"ImageAnalysis: Failed to open interactive dialog: {tb}")
            try:
                errFile = os.path.join(os.path.dirname(__file__), "last_dialog_error.txt")
                with open(errFile, "w", encoding="utf-8") as f:
                    f.write(tb)
            except Exception:
                pass
            htmlContent = _formatResponseHtml(resultText)
            ui.browseableMessage(htmlContent, title=title, isHtml=True)

    def _onError(self, errorMessage):
        _playErrorChime()
        ui.message(errorMessage)

    def applySettings(self):
        """Called when settings are saved."""
        pass

    def terminate(self):
        if self._beepProgress:
            self._beepProgress.stop()
            self._beepProgress = None
        self._isAnalyzing = False
