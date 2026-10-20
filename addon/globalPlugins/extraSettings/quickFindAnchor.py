"""Plain-text matching independent of NVDA objects or GUI calls."""
import hashlib
import re

MAX_DOCUMENT = 1_000_000
MAX_MATCHES = 1000
CONTEXT = 100


def clean(text):
    return " ".join(text.split())


def digest(text):
    return hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()


def occurrences(source, term):
    if not term:
        return []
    result = []
    for match in re.finditer("(?=" + re.escape(term) + ")", source, re.IGNORECASE):
        result.append(match.start())
        if len(result) > MAX_MATCHES:
            raise ValueError("matches")
    return result


def neighbours(source, offset, length):
    return clean(source[max(0, offset-CONTEXT):offset]), clean(source[offset+length:offset+length+CONTEXT])


def capture(source, term, offset, docKey, document):
    term = term.strip()
    if not term:
        raise ValueError("empty")
    if len(term) > 1000 or len(source) > MAX_DOCUMENT:
        raise ValueError("large")
    positions = occurrences(source, term)
    # The start of the expanded line is known, so identical neighbouring lines
    # cannot be confused at capture time. Leading whitespace may be stripped.
    selected = next((i for i, pos in enumerate(positions)
                     if pos >= offset and not source[offset:pos].strip()), None)
    if selected is None:
        raise ValueError("capture")
    pos = positions[selected]
    before, after = neighbours(source, pos, len(term))
    return dict(name=clean(term)[:500], docKey=docKey, document=document, text=term,
                before=before, after=after, offset=pos, ordinal=selected,
                digest=digest(source), shortcut="", occurrenceCount=len(positions),
                lineNumber=len(re.findall(r"\r\n|\r|\n", source[:pos])) + 1)


def resolve(source, entry):
    if len(source) > MAX_DOCUMENT:
        raise ValueError("large")
    positions = occurrences(source, entry["text"])
    if not positions:
        return []
    if digest(source) == entry["digest"] and entry["offset"] in positions:
        return [positions.index(entry["offset"])]
    if len(positions) == 1:
        before, after = neighbours(source, positions[0], len(entry["text"]))
        if entry.get("occurrenceCount", entry["ordinal"] + 1) > 1 and not (
            (entry["before"] and before.endswith(entry["before"]))
            or (entry["after"] and after.startswith(entry["after"]))
        ):
            # A repeated target may have been deleted, leaving an unrelated
            # occurrence. Uniqueness now is not proof that it is the saved one.
            return []
        return [0]
    scores = []
    for pos in positions:
        before, after = neighbours(source, pos, len(entry["text"]))
        scores.append(int(bool(entry["before"]) and before.endswith(entry["before"]))
                      + int(bool(entry["after"]) and after.startswith(entry["after"])))
    highest = max(scores)
    choices = [i for i, score in enumerate(scores) if score == highest]
    # With no unique context, ask the user. Never choose by nearest line number
    # after the document has changed.
    return choices if highest else list(range(len(positions)))
