from pathlib import Path

from job_radar.watch.page_text import is_readable, page_snapshot

PAGES = Path(__file__).resolve().parents[2] / "fixtures" / "pages" / "careers"


def html(body: str, head: str = "") -> str:
    return f"<html><head><title>Acme careers</title>{head}</head><body>{body}</body></html>"


# --- visible lines --------------------------------------------------------------------


def test_real_page_lines_are_clean_and_deduplicated() -> None:
    snap = page_snapshot((PAGES / "mozilla.org.html").read_text())
    assert snap.lines[0] == "Mozilla Careers — All open positions at Mozilla"
    assert "Staff Data Scientist, Ads" in snap.lines
    assert snap.lines.count("Remote Germany") == 1  # 6 times on the page
    assert not any("function(" in line or "{" in line for line in snap.lines)


def test_scripts_styles_navigation_and_footer_are_dropped() -> None:
    snap = page_snapshot(
        html(
            "<nav>Home About</nav><script>var x = 1;</script><style>p{}</style>"
            "<main><h2>Data Analyst</h2></main><footer>© Acme</footer><noscript>JS</noscript>",
        )
    )
    assert snap.lines == ("Acme careers", "Data Analyst")


def test_whitespace_is_normalized() -> None:
    snap = page_snapshot(html("<p>  Chef   de\u00a0projet \n</p><p>\t</p>"))
    assert snap.lines == ("Acme careers", "Chef de projet")


def test_hash_ignores_markup_changes() -> None:
    a = page_snapshot(html('<div class="x"><p>Data Analyst</p></div>'))
    b = page_snapshot(html('<section id="y"><h3>Data Analyst</h3></section>'))
    assert a.content_hash == b.content_hash


# --- JavaScript-only pages ---------------------------------------------------------------


def test_javascript_only_page_is_not_readable() -> None:
    assert not is_readable(page_snapshot((PAGES / "metacareers.com.html").read_text()))


def test_job_board_page_is_readable() -> None:
    snap = page_snapshot((PAGES / "ikea.com.html").read_text())
    assert is_readable(snap)
    assert "Et si tu nous rejoignais en alternance ?" in snap.lines


# --- changes ------------------------------------------------------------------------------
