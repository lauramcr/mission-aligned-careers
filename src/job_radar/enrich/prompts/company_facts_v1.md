You extract facts about one organisation from the text of its website, for a job seeker.
Report only what the text supports. When the text does not say, use null, "unknown" or an
empty list. Do not guess from the name alone.

Organisation: {name}
Website: {website}

Use ONLY these ids.

Themes (what the organisation works on; one primary theme, optional secondary themes):
{themes}

Sectors (core business sectors, and sectors of major clients):
{sectors}

Organisation types: {org_types}
Business models: {business_models}

Field guidance:
- short_description: one plain sentence, max 30 words, in English.
- core_sectors: only sectors that are the organisation's own core business.
- major_client_sectors: sectors of clients it explicitly names or targets.
- data_centrality: "core" if data/software is the product; "supporting" if data supports a
  physical product or service; "minimal" otherwise.
- funding_status: "funded" if it mentions investors, grants or a funding round; "revenue" if it
  clearly sells to customers; otherwise "unknown".
- headcount_estimate: only if the text gives a number of people.
- office_locations: cities where it says it has offices.
- greenwashing_risk: true if climate claims are vague, marketing-led or unsupported.
- confidence: 0 to 1, how sure you are about theme, org type and sectors overall.
- evidence: for each non-empty field you filled, a short quote (max 20 words) from the text.

The website text follows.
