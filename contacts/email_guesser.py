import dns.resolver
import re
import logging
from urllib.parse import urlparse

_logger = logging.getLogger(__name__)

_mx_cache: dict[str, list | None] = {}

ATS_DOMAINS = {
    "boards.greenhouse.io", "api.lever.co", "jobs.lever.co",
    "api.ashbyhq.com", "jobs.ashbyhq.com", "boards.eu.greenhouse.io",
    "jobs.workable.com", "jobs.smartrecruiters.com", "apply.workable.com",
    "jobs.jobvite.com", "careers.jobscore.com", "bamboohr.com",
    "himalayas.app", "remoteok.com", "remotive.com", "weworkremotely.com",
    "justremote.co", "nodesk.co", "dynamitejobs.com", "workingnomads.co",
    "arbeitnow.com", "jobicy.com", "themuse.com", "wellfound.com",
    "angel.co", "linkedin.com", "indeed.com", "glassdoor.com",
    "dribbble.com", "larajobs.com", "vuejobs.com", "findwork.dev",
}


def get_domain_from_url(url: str) -> str:
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path.split("/")[0]
    domain = domain.lower().removeprefix("www.")
    if domain in ATS_DOMAINS:
        return ""
    return domain


def _domain_from_name(company_name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]", "", company_name.lower())
    return f"{cleaned}.com"


def _get_mx_records(domain: str) -> list | None:
    if domain in _mx_cache:
        return _mx_cache[domain]
    try:
        answers = dns.resolver.resolve(domain, "MX")
        records = sorted(answers, key=lambda r: r.preference)
        _mx_cache[domain] = records
        return records
    except Exception:
        _mx_cache[domain] = None
        return None


def has_valid_mx(domain: str) -> bool:
    records = _get_mx_records(domain)
    return records is not None and len(records) > 0


def _split_name(contact_name: str) -> tuple[str, str]:
    parts = contact_name.strip().split()
    if len(parts) >= 2:
        return parts[0].lower(), parts[-1].lower()
    if len(parts) == 1:
        return parts[0].lower(), ""
    return "", ""


def guess_emails(
    company_name: str, company_url: str, contact_name: str = ""
) -> list[dict]:
    domain = get_domain_from_url(company_url)
    if not domain:
        domain = _domain_from_name(company_name)

    if not has_valid_mx(domain):
        return []

    candidates = []

    if not contact_name:
        return []
    else:
        first, last = _split_name(contact_name)
        if not first:
            return candidates

        patterns = [
            (f"{first}.{last}@{domain}", 0.55) if last else None,
            (f"{first}@{domain}", 0.45),
            (f"{first[0]}{last}@{domain}", 0.40) if last else None,
        ]
        for entry in patterns:
            if entry is None:
                continue
            email, confidence = entry
            candidates.append({
                "email": email,
                "confidence": confidence,
                "source": "guess_named",
            })

    return candidates
