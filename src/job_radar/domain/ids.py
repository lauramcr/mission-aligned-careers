"""Stable identifiers for companies and job postings.

`company_id` is the registered domain (e.g. `jobs.acme.com` -> `acme.com`), so the same
company found by different sources maps to one key. Public suffixes such as `co.uk`,
`gouv.fr` or `github.io` are respected via the Public Suffix List bundled with tldextract;
the extractor is configured never to fetch an updated list over the network.
The reserved `.example` suffix (RFC 2606) is accepted too: the demo's fictional companies
use it.
"""

import re
from urllib.parse import urlsplit

import tldextract

_EXTRACT = tldextract.TLDExtract(
    suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True
)
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_DEMO_SUFFIX = "example"


def company_id(url_or_domain: str) -> str:
    """Return the normalized registered domain for a URL or bare domain."""
    raw = url_or_domain.strip()
    if not raw or any(ch.isspace() for ch in raw):
        raise ValueError(f"Not a valid domain or URL: {url_or_domain!r}")
    if not _SCHEME.match(raw):
        raw = f"http://{raw}"
    host = (urlsplit(raw).hostname or "").rstrip(".").lower()
    labels = host.split(".")
    if len(labels) >= 2 and labels[-1] == _DEMO_SUFFIX and labels[-2]:
        return f"{labels[-2]}.{_DEMO_SUFFIX}"
    parts = _EXTRACT(host)
    if not parts.domain or not parts.suffix:
        raise ValueError(f"Not a valid domain or URL: {url_or_domain!r}")
    return f"{parts.domain}.{parts.suffix}"


def job_id(ats: str, slug: str, native_id: str) -> str:
    """Return the stable job ID `{ats}:{slug}:{native_id}`."""
    ats, slug, native_id = ats.strip().lower(), slug.strip(), native_id.strip()
    if not (ats and slug and native_id):
        raise ValueError("ats, slug and native_id must all be non-empty")
    if ":" in ats or ":" in slug:
        raise ValueError("ats and slug must not contain ':'")
    return f"{ats}:{slug}:{native_id}"
