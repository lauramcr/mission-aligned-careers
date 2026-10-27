"""Rough facts from keywords (free, no LLM): list descriptions + website text."""

from pathlib import Path

import pytest

from job_radar.domain.facts import DataCentrality
from job_radar.enrich.keywords import KEYWORD_CONFIDENCE, KeywordExtractor
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Taxonomy

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def taxonomy() -> Taxonomy:
    return load_profile(REPO / "config" / "criteria.example.yaml", REPO / "packs").taxonomy


def text(described: str, website: str = "") -> str:
    parts = [f"Described on Norrsken Impact/100: {described}"]
    if website:
        parts.append(f"Website text:\n{website}")
    return "\n\n".join(parts)


@pytest.mark.parametrize(
    ("described", "website", "themes"),
    [
        (
            "Turns toxic industrial emissions into building blocks for brand-new products. · "
            "Sectors: cleantech, Clean Energy & Climate Tech",
            "Aerleum transforms atmospheric CO₂ into cost-competitive synthetic fuels. "
            "Our carbon capture technology…",
            {"carbon_capture_removal"},
        ),
        (
            "What if, we could make second hand gadgets the first hand choice? · "
            "Sectors: e-commerce",
            "",
            {"repair_refurbishment"},
        ),
        (
            "24/7 clean energy trading and management. · Sectors: energy",
            "",
            {"renewable_energy", "grid_flexibility_energy_data"},
        ),
        (
            "Enabling more food for a growing population with sustainable, insect-based animal "
            "feed. · Sectors: agriculture",
            "",
            {"sustainable_agriculture"},
        ),
    ],
)
def test_primary_theme(taxonomy: Taxonomy, described: str, website: str, themes: set[str]) -> None:
    facts = KeywordExtractor(taxonomy).extract(
        name="X", website="https://x.com", text=text(described, website)
    )
    assert facts.primary_theme in themes


def test_no_climate_keywords_means_no_theme(taxonomy: Taxonomy) -> None:
    described = (
        "Studies people who beat cancer then turns their biology into cures. · Sectors: biotech"
    )
    facts = KeywordExtractor(taxonomy).extract(
        name="Cure51", website="https://c.com", text=text(described)
    )
    assert facts.primary_theme is None


def test_rough_facts_are_marked_and_low_confidence(taxonomy: Taxonomy) -> None:
    extractor = KeywordExtractor(taxonomy)
    facts = extractor.extract(name="X", website="https://x.com", text=text("Solar farms."))
    assert facts.extracted_by == "keywords"
    assert facts.confidence == KEYWORD_CONFIDENCE < 0.6
    assert "solar" in facts.evidence["primary_theme"]
    assert extractor.version == "keywords_v2" and extractor.metered is False


def test_secondary_themes_need_repeated_mentions(taxonomy: Taxonomy) -> None:
    website = "Recycling plant. We recycle batteries. Recycled lithium. Solar."
    facts = KeywordExtractor(taxonomy).extract(
        name="X", website="https://x.com", text=text("Battery recycling", website)
    )
    assert facts.primary_theme == "circular_economy"
    assert "renewable_energy" not in facts.secondary_themes  # one mention only
    assert "mining_transition_metals" not in facts.secondary_themes


def test_business_model_data_role_and_org_type(taxonomy: Taxonomy) -> None:
    saas = KeywordExtractor(taxonomy).extract(
        name="X",
        website="https://x.com",
        text=text("Carbon accounting software platform with analytics dashboards."),
    )
    assert saas.business_model == "b2b_saas"
    assert saas.data_centrality is DataCentrality.CORE
    assert saas.org_type == "startup"
    fund = KeywordExtractor(taxonomy).extract(
        name="X",
        website="https://x.com",
        text=text("A venture capital climate fund that invests in early-stage startups."),
    )
    assert fund.org_type == "investor"


def test_sector_mentions_are_clients_never_a_gate(taxonomy: Taxonomy) -> None:
    facts = KeywordExtractor(taxonomy).extract(
        name="X",
        website="https://x.com",
        text=text(
            "Drilling services for oil and gas operators.",
            "Our methane sensors are used by airlines and airports.",
        ),
    )
    # "helps oil and gas companies decarbonise" must not hide a company: rough facts never gate
    assert facts.core_sectors == []
    assert {"oil_and_gas", "aviation"} <= set(facts.major_client_sectors)


@pytest.mark.parametrize(
    ("name", "described", "website"),
    [
        # one stray word on a homepage is not evidence of a climate theme
        ("Notion", "", "The AI workspace that works for you. Stop the flood of messages."),
        (
            "Ada Health",
            "Saving patients years of life by shortening the time to diagnosis. · Sectors: health",
            "Symptoms during a heatwave: when to see a doctor.",
        ),
        # satellites alone are connectivity as often as earth observation
        (
            "Astranis",
            "Satellites to connect billions of people without access to internet.",
            "",
        ),
    ],
)
def test_weak_evidence_gives_no_theme(
    taxonomy: Taxonomy, name: str, described: str, website: str
) -> None:
    raw = text(described, website) if described else f"Website text:\n{website}"
    facts = KeywordExtractor(taxonomy).extract(name=name, website="https://x.com", text=raw)
    assert facts.primary_theme is None


def test_generic_words_do_not_make_data_core(taxonomy: Taxonomy) -> None:
    facts = KeywordExtractor(taxonomy).extract(
        name="X",
        website="https://x.com",
        text=text("Solar farms for farmers.", "We use AI and data on our platform."),
    )
    assert facts.data_centrality is DataCentrality.UNKNOWN


def test_analytics_wording_makes_data_core(taxonomy: Taxonomy) -> None:
    facts = KeywordExtractor(taxonomy).extract(
        name="X",
        website="https://x.com",
        text=text("Grid analytics for solar farms.", "Machine learning monitoring of assets."),
    )
    assert facts.data_centrality is DataCentrality.CORE
