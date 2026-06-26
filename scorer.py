import re

BIG_COMPANIES = {
    "google", "meta", "facebook", "amazon", "apple", "microsoft", "netflix",
    "uber", "lyft", "airbnb", "salesforce", "oracle", "ibm", "cisco", "intel",
    "qualcomm", "adobe", "vmware", "paypal", "twitter", "snap", "pinterest",
    "doordash", "instacart", "robinhood", "coinbase", "stripe", "spotify",
    "shopify", "atlassian", "slack", "zoom", "docusign", "snowflake",
    "palantir", "crowdstrike", "servicenow", "workday", "splunk", "mongodb",
    "elastic", "datadog", "cloudflare", "twilio", "hashicorp", "gitlab",
    "figma", "discord", "tiktok", "bytedance", "samsung", "sony", "dell",
    "hp", "lenovo", "ericsson", "nokia", "accenture", "deloitte", "pwc",
    "kpmg", "wipro", "infosys", "tcs", "cognizant", "capgemini", "jpmorgan",
    "goldman sachs", "morgan stanley", "visa", "mastercard", "openai",
    "anthropic", "sap", "siemens", "samsara", "1password", "agilebits",
    "nbcuniversal", "nbc", "mutual of omaha", "digitalocean", "leidos",
}


def is_big_company(company_name: str) -> bool:
    name_lower = company_name.lower().strip()
    return any(big in name_lower for big in BIG_COMPANIES)


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
    if is_big_company(job.get("company_name", "")):
        return -1.0
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
