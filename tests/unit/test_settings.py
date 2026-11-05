"""File locations: today's defaults, overridable with JOB_RADAR_* environment variables."""

from pathlib import Path

from job_radar.settings import RadarPaths, find_cv, read_cv


def tiny_pdf(text: str) -> bytes:
    """A one-page PDF showing `text` (built by hand: no PDF writer needed)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return out


def test_defaults_are_the_personal_layout() -> None:
    paths = RadarPaths.from_env({})
    # Everything personal you edit is in config/; what the tool writes is in data/.
    assert paths.criteria == Path("config/criteria.yaml")
    assert paths.companies == Path("config/companies.csv")
    assert paths.pending == Path("config/companies_pending.csv")
    assert paths.sources == Path("config/sources.yaml")
    assert paths.decisions == Path("config/decisions.csv")
    assert paths.cv == Path("config/cv")  # config/cv.pdf, .docx, .md or .txt
    assert paths.db == Path("data/radar.duckdb")
    assert paths.scores_csv == Path("data/scores.csv")
    assert paths.replay_dir is None  # live


def test_read_cv(tmp_path: Path) -> None:
    assert read_cv(tmp_path / "missing.md") is None
    (tmp_path / "cv.md").write_text("  \n")
    assert read_cv(tmp_path / "cv.md") is None  # empty = no CV yet
    (tmp_path / "cv.md").write_text("SQL, Python")
    assert read_cv(tmp_path / "cv.md") == "SQL, Python"


def test_environment_overrides_each_path() -> None:
    paths = RadarPaths.from_env(
        {
            "JOB_RADAR_CRITERIA": "demo_config/criteria.yaml",
            "JOB_RADAR_DB": "demo-run/radar.duckdb",
            "JOB_RADAR_REPLAY_DIR": "demo_config",
            "UNRELATED": "x",
        }
    )
    assert paths.criteria == Path("demo_config/criteria.yaml")
    assert paths.db == Path("demo-run/radar.duckdb")
    assert paths.replay_dir == Path("demo_config")
    assert paths.packs == Path("packs")  # untouched


def test_an_empty_replay_dir_means_live() -> None:
    assert RadarPaths.from_env({"JOB_RADAR_REPLAY_DIR": ""}).replay_dir is None


def test_find_cv_by_name_whatever_the_format(tmp_path: Path) -> None:
    stem = tmp_path / "cv"
    assert find_cv(stem) is None
    (tmp_path / "cv.md").write_text("SQL")
    assert find_cv(stem) == tmp_path / "cv.md"
    (tmp_path / "cv.pdf").write_bytes(tiny_pdf("SQL"))
    assert find_cv(stem) == tmp_path / "cv.pdf"  # PDF, then Word, then Markdown, then text
    assert find_cv(tmp_path / "cv.md") == tmp_path / "cv.md"  # an exact file is used as is


def test_read_cv_from_pdf_and_word(tmp_path: Path) -> None:
    from docx import Document

    (tmp_path / "pdf").mkdir()
    (tmp_path / "pdf" / "cv.pdf").write_bytes(tiny_pdf("Data analyst: SQL, Python, dbt"))
    assert "SQL, Python, dbt" in (read_cv(tmp_path / "pdf" / "cv") or "")

    document = Document()
    document.add_paragraph("Data analyst")
    document.add_table(rows=1, cols=1).cell(0, 0).text = "Skills: Power BI, Airflow"
    (tmp_path / "word").mkdir()
    document.save(str(tmp_path / "word" / "cv.docx"))
    text = read_cv(tmp_path / "word" / "cv") or ""
    assert "Data analyst" in text and "Power BI, Airflow" in text  # tables too


def test_a_cv_without_readable_text_is_no_cv(tmp_path: Path) -> None:
    (tmp_path / "cv.pdf").write_bytes(b"%PDF-1.4 not really")  # broken or scanned
    assert read_cv(tmp_path / "cv") is None
