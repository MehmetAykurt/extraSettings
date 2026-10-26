"""Small, bounded Google web-translation client; no browser or credentials.

The text is POSTed over HTTPS, never placed in a URL or an add-on log.
This is an unofficial endpoint, so every response is validated defensively.
"""
import json
import locale
from pathlib import Path
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ENDPOINT = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&dt=t"
MAX_TEXT = 5000
MAX_RESPONSE = 256 * 1024
NETWORK_TIMEOUT = 8

with Path(__file__).with_name("translationLanguages.json").open(encoding="utf-8-sig") as stream:
    _LANGUAGES = json.load(stream)["languages"]
LANGUAGE_CODES = frozenset(item["code"] for item in _LANGUAGES)


class TranslationFailure(Exception):
    """Contains only a fixed error category, never source text or a URL."""
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _openTranslation(request):
    try:
        return urlopen(request, timeout=NETWORK_TIMEOUT)
    except URLError as error:
        reason = error.reason
        if not isinstance(reason, ssl.SSLCertVerificationError) or reason.verify_code != 85:
            raise
        # Python 3.13 strict validation rejects some trusted legacy Windows
        # roots without Authority Key Identifier. Retry this request only with
        # OpenSSL's compatible chain rules; keep roots, hostname and CERT_REQUIRED.
        context = ssl.create_default_context()
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        return urlopen(request, timeout=NETWORK_TIMEOUT, context=context)


def languageChoices(interfaceLanguage):
    language = "tr" if interfaceLanguage.replace("-", "_").split("_")[0].lower() == "tr" else "en"
    return sorted(((item["code"], item.get(language) or item["en"]) for item in _LANGUAGES),
                  key=lambda item: locale.strxfrm(item[1].casefold()))


def parseTranslation(raw):
    try:
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
            raise ValueError
        segments = payload[0]
        if not segments or any(not isinstance(segment, list) or not segment for segment in segments):
            raise ValueError
        pieces = []
        for segment in segments:
            if segment[0] is None:
                continue
            if not isinstance(segment[0], str):
                raise ValueError
            pieces.append(segment[0])
        result = "".join(pieces).strip()
        if not result:
            raise ValueError
        return result
    except (ValueError, UnicodeError, TypeError, IndexError):
        raise TranslationFailure("response") from None


def fetchTranslation(text, target):
    if target not in LANGUAGE_CODES:
        raise TranslationFailure("language")
    if not isinstance(text, str) or not text.strip():
        raise TranslationFailure("empty")
    if len(text) > MAX_TEXT:
        raise TranslationFailure("length")
    data = urlencode({"q": text}).encode("ascii")
    request = Request(
        ENDPOINT + "&" + urlencode({"tl": target}), data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                 "User-Agent": "ExtraSettings/2026.10.26", "Accept": "application/json"},
        method="POST",
    )
    try:
        with _openTranslation(request) as response:
            if response.status != 200:
                raise TranslationFailure("unavailable")
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise TranslationFailure("response")
        return parseTranslation(raw)
    except HTTPError as error:
        reason = "limited" if error.code in (403, 429) else "unavailable"
        error.close()
        raise TranslationFailure(reason) from None
    except (TimeoutError, socket.timeout):
        raise TranslationFailure("timeout") from None
    except URLError as error:
        reason = "timeout" if isinstance(error.reason, (TimeoutError, socket.timeout)) else "unavailable"
        raise TranslationFailure(reason) from None
    except (OSError, UnicodeError, ValueError):
        raise TranslationFailure("unavailable") from None
