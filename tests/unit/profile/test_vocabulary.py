"""Your own vocabulary (config/my_vocabulary.yaml): themes, sectors and role families you add,
merged with the pack's, so the tool works beyond the pack's field."""

import shutil
from pathlib import Path

import pytest

from job_radar.enrich.extractor import ClaudeCodeExtractor, render_prompt
from job_radar.enrich.keywords import KeywordExtractor
from job_radar.matching.prefilter import pack_family_of
from job_radar.profile.loader import ProfileError, load_profile

REPO = Path(__file__).resolve().parents[3]
VOCABULARY = """\
themes:
  biotech:
    label: Biotech
    keywords: [biotech, biotechnology, synthetic biology]
sectors:
  gambling:
    label: Gambling
    keywords: [casino, betting]
role_families:
  biostatistics:
    label: Biostatistics
    title_keywords: [biostatistician, biostatistics]
"""


@pytest.fixture
def criteria(tmp_path: Path) -> Path:
    shutil.copy(REPO / "tests/fixtures/profile/criteria.yaml", tmp_path / "criteria.yaml")
    return tmp_path / "criteria.yaml"


def test_your_terms_join_the_pack(criteria: Path) -> None:
    plain = load_profile(criteria, REPO / "packs").taxonomy
    (criteria.parent / "my_vocabulary.yaml").write_text(VOCABULARY)
    taxonomy = load_profile(criteria, REPO / "packs").taxonomy

    assert taxonomy.themes["biotech"].label == "Biotech"
    assert "biotechnology" in taxonomy.themes["biotech"].description  # what Claude reads
    assert taxonomy.sectors["gambling"].label == "Gambling"
    assert taxonomy.role_families["biostatistics"].title_keywords == [
        "biostatistician",
        "biostatistics",
    ]
    assert set(plain.themes) < set(taxonomy.themes)  # the pack's are all still there
    assert plain.vocabulary_hash == "" and taxonomy.vocabulary_hash  # facts are redone


def test_your_terms_are_used(criteria: Path) -> None:
    (criteria.parent / "my_vocabulary.yaml").write_text(VOCABULARY)
    profile = load_profile(criteria, REPO / "packs")
    facts = KeywordExtractor(profile.taxonomy).extract(
        name="Helix",
        website="https://helix.example",
        text="Described on a list: Synthetic biology for food. · Biotech startup",
    )
    assert facts.primary_theme == "biotech"
    assert pack_family_of("Senior Biostatistician", profile) == "biostatistics"
    assert "biotech" in render_prompt(
        profile.taxonomy, name="Helix", website="https://helix.example"
    )
    plain = load_profile(REPO / "tests/fixtures/profile/criteria.yaml", REPO / "packs")
    assert KeywordExtractor(profile.taxonomy).version != KeywordExtractor(plain.taxonomy).version
    claude = ClaudeCodeExtractor(profile.taxonomy, executable="/bin/true")
    assert profile.taxonomy.vocabulary_hash in claude.version


@pytest.mark.parametrize(
    ("vocabulary", "problem"),
    [
        (
            "themes:\n  ocean:\n    label: Ocean\n    keywords: [ocean]\n",
            "themes.ocean: already in the pack",
        ),
        ("themes:\n  biotech:\n    label: Biotech\n    keywords: []\n", "biotech"),
        ("colours:\n  red: {}\n", "colours"),
    ],
)
def test_bad_vocabulary_is_explained(criteria: Path, vocabulary: str, problem: str) -> None:
    (criteria.parent / "my_vocabulary.yaml").write_text(vocabulary)
    with pytest.raises(ProfileError) as exc:
        load_profile(criteria, REPO / "packs")
    assert any(problem in e for e in exc.value.errors), exc.value.errors
