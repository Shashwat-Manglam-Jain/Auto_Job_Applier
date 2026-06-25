import re

STARTUP_INDICATORS = [
    "startup", "seed", "series a", "series b", "early stage",
    "founding", "co-founder", "yc ", "y combinator", "techstars",
    "small team", "fast-paced", "venture", "bootstrap",
]

REMOTE_FIRST_INDICATORS = [
    "remote-first", "remote first", "fully remote", "100% remote",
    "distributed team", "work from anywhere", "async",
]


def score_company(job: dict) -> float:
    score = 0.0
    title_lower = job.get("title", "").lower()
    desc_lower = job.get("description", "")[:1000].lower()
    combined = f"{title_lower} {desc_lower}"

    if job.get("posted_at"):
        score += 0.2

    for kw in STARTUP_INDICATORS:
        if kw in combined:
            score += 0.3
            break

    for kw in REMOTE_FIRST_INDICATORS:
        if kw in combined:
            score += 0.2
            break

    if job.get("salary_min") or job.get("salary_max"):
        score += 0.1

    tags = job.get("tags", [])
    if len(tags) >= 3:
        score += 0.1

    if job.get("company_url"):
        score += 0.1

    return min(score, 1.0)
