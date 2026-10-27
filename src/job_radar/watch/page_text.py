"""Turn a careers page into its visible-text lines (a snapshot), so the tool can read plain
careers pages: which lines mention your roles, and whether the page shows anything at all
without JavaScript.
"""

import hashlib
from dataclasses import dataclass

from bs4 import BeautifulSoup

_DROP_TAGS = ["script", "style", "noscript", "template", "svg", "iframe", "nav", "footer", "header"]
MIN_READABLE_CHARS = 200


@dataclass(frozen=True)
class PageSnapshot:
    """A careers page as its visible lines, with a hash to tell whether it changed."""

    lines: tuple[str, ...]
    content_hash: str

    @property
    def chars(self) -> int:
        """How much visible text the page has (very little usually means JavaScript-only)."""
        return sum(len(line) for line in self.lines)


def page_snapshot(html: str) -> PageSnapshot:
    """The visible text lines of a page, without scripts, menus, headers and footers."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text() if soup.title else ""
    for tag in soup(_DROP_TAGS):
        tag.decompose()
    if soup.head:
        soup.head.decompose()
    raw = [title, *soup.get_text("\n").splitlines()]
    lines = tuple(dict.fromkeys(n for n in (" ".join(r.split()) for r in raw) if len(n) > 1))
    digest = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    return PageSnapshot(lines=lines, content_hash=digest)


def is_readable(snapshot: PageSnapshot, min_chars: int = MIN_READABLE_CHARS) -> bool:
    """False for pages whose content only appears with JavaScript."""
    return snapshot.chars >= min_chars
