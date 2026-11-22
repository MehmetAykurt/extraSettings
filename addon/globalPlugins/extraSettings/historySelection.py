"""Which speech history entries are marked, and what a copy command should copy."""

ENTRY_SEPARATOR = "\r\n"

Items = list[tuple[int, str]]


class MarkedEntries:
    """Entry ids in the order the user marked them (the first marked comes first)."""

    def __init__(self) -> None:
        self._order: list[int] = []

    def __contains__(self, entryId: object) -> bool:
        return entryId in self._order

    def __len__(self) -> int:
        return len(self._order)

    def setMarked(self, entryId: int, marked: bool) -> None:
        """Mark or unmark an entry. Marking again later moves it to the end."""
        if marked:
            if entryId not in self._order:
                self._order.append(entryId)
        elif entryId in self._order:
            self._order.remove(entryId)

    def prune(self, validIds: set[int]) -> None:
        """Forget marks of entries that no longer exist, keeping the order."""
        self._order = [entryId for entryId in self._order if entryId in validIds]

    def clear(self) -> None:
        """Remove every mark."""
        self._order = []

    def ordered(self) -> list[int]:
        """Return a copy of the marked ids in marking order."""
        return list(self._order)


def _markedTexts(items: Items, marked: MarkedEntries) -> list[str]:
    """Texts of the marked entries that still exist, in marking order."""
    texts = dict(items)
    return [texts[entryId] for entryId in marked.ordered() if entryId in texts]


def buildCopyText(items: Items, marked: MarkedEntries, focusedId: int | None) -> str | None:
    """Text to copy: the marked entries in marking order, else the focused entry.

    Returns None when there is nothing to copy.
    """
    texts = _markedTexts(items, marked)
    if texts:
        return ENTRY_SEPARATOR.join(texts)
    if focusedId is not None:
        focused = dict(items).get(focusedId)
        if focused is not None:
            return focused
    return None


def copyCount(items: Items, marked: MarkedEntries, focusedId: int | None) -> int:
    """How many entries buildCopyText would copy."""
    count = len(_markedTexts(items, marked))
    if count:
        return count
    return 1 if focusedId is not None and focusedId in dict(items) else 0
