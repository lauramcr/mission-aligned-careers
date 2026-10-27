"""Classify a careers link: which kind of board is it, and the company's slug on it."""

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit


class BoardKind(StrEnum):
    """The kind of careers link: a job board with a feed we can read, a site we can't read, a
    plain page, or none.
    """

    ASHBY = "ashby"
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    RECRUITEE = "recruitee"
    WORKABLE = "workable"
    TEAMTAILOR = "teamtailor"  # slug: the careers site's host (often a custom domain)
    PERSONIO = "personio"
    BAMBOOHR = "bamboohr"
    WTTJ = "wttj"
    MAKESENSE = "makesense"
    OCCUPOP = "occupop"
    LINKEDIN = "linkedin"
    PAGE = "page"  # any other careers page: watched for changes
    NONE = "none"  # no careers link

    @property
    def has_feed(self) -> bool:
        """An official structured job feed exists for this kind."""
        return self in {
            BoardKind.ASHBY,
            BoardKind.GREENHOUSE,
            BoardKind.LEVER,
            BoardKind.RECRUITEE,
            BoardKind.WORKABLE,
            BoardKind.TEAMTAILOR,
            BoardKind.PERSONIO,
            BoardKind.BAMBOOHR,
        }

    @property
    def manual_only(self) -> bool:
        """Can't be read automatically; the user checks it by hand."""
        return self is BoardKind.LINKEDIN


@dataclass(frozen=True)
class BoardRef:
    """A board kind and the company's account (slug) on it."""

    kind: BoardKind
    slug: str | None


# (host pattern, path pattern capturing the slug, kind); first match wins.
_RULES: list[tuple[re.Pattern[str], re.Pattern[str] | None, BoardKind]] = [
    (re.compile(r"^jobs\.ashbyhq\.com$"), re.compile(r"^/([^/]+)"), BoardKind.ASHBY),
    (
        re.compile(r"^(boards|job-boards)(\.eu)?\.greenhouse\.io$"),
        re.compile(r"^/([^/]+)"),
        BoardKind.GREENHOUSE,
    ),
    # apply.workable.com/{slug}; /j/{shortcode} links don't name the account
    (
        re.compile(r"^apply\.workable\.com$"),
        re.compile(r"^/(?!j/|api/)([^/]+)"),
        BoardKind.WORKABLE,
    ),
    (re.compile(r"^jobs(\.eu)?\.lever\.co$"), re.compile(r"^/([^/]+)"), BoardKind.LEVER),
    (
        re.compile(r"(^|\.)welcometothejungle\.com$"),
        re.compile(r"/companies(?:-v1)?/([^/]+)"),
        BoardKind.WTTJ,
    ),
    (re.compile(r"^jobs\.makesense\.org$"), re.compile(r"/projects/([^/]+)"), BoardKind.MAKESENSE),
    (re.compile(r"(^|\.)linkedin\.com$"), re.compile(r"/company/([^/]+)"), BoardKind.LINKEDIN),
]
_OCCUPOP = re.compile(r"^([^.]+)\.occupop-careers\.com$")
_SLUG_REQUIRED = {BoardKind.WORKABLE}
_NOT_ACCOUNT = r"(?!(?:www|career|careers|app|api|apply|help|support)\.)"
# Boards where the company's account is the subdomain.
_SUBDOMAIN_RULES: list[tuple[re.Pattern[str], BoardKind]] = [
    (re.compile(rf"^{_NOT_ACCOUNT}([^.]+)\.recruitee\.com$"), BoardKind.RECRUITEE),
    (re.compile(rf"^{_NOT_ACCOUNT}([^.]+)\.workable\.com$"), BoardKind.WORKABLE),
    (re.compile(rf"^{_NOT_ACCOUNT}([^.]+)\.jobs\.personio\.(?:de|com)$"), BoardKind.PERSONIO),
    (re.compile(rf"^{_NOT_ACCOUNT}([^.]+)\.bamboohr\.com$"), BoardKind.BAMBOOHR),
]
_TEAMTAILOR = re.compile(rf"^{_NOT_ACCOUNT}[^.]+\.teamtailor\.com$")


def classify_careers_url(url: str) -> BoardRef:
    """What kind of careers link this is, and the company's account on the board (from the
    address alone, no request).
    """
    url = url.strip()
    if not url:
        return BoardRef(BoardKind.NONE, None)
    parts = urlsplit(url if "://" in url else f"https://{url}")
    host = (parts.hostname or "").lower()

    for rule, kind in _SUBDOMAIN_RULES:
        account = rule.match(host)
        if account:
            return BoardRef(kind, account.group(1))
    if _TEAMTAILOR.match(host):
        return BoardRef(BoardKind.TEAMTAILOR, host)

    occupop = _OCCUPOP.match(host)
    if occupop:
        return BoardRef(BoardKind.OCCUPOP, occupop.group(1))

    for host_rule, path_rule, kind in _RULES:
        if host_rule.search(host):
            match = path_rule.search(parts.path) if path_rule else None
            if match is None and kind in _SLUG_REQUIRED:
                break  # e.g. a single Workable job link: watched as a page
            return BoardRef(kind, match.group(1) if match else None)
    return BoardRef(BoardKind.PAGE, None)
