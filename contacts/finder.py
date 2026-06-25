import asyncio
import logging
from contacts.search_engine import search_contacts
from contacts.email_guesser import get_domain_from_url, guess_emails, verify_email_exists
from contacts.website_scraper import scrape_company_contacts

logger = logging.getLogger(__name__)


def _is_obviously_bad_email(email: str) -> bool:
    """
    Detect emails that are clearly invalid patterns, e.g. 'fly.io@fly.io'
    where the local part matches the domain name.
    """
    try:
        local, domain = email.lower().rsplit("@", 1)
    except ValueError:
        return True

    # Strip TLD to get the bare domain name (e.g. "fly.io" -> "fly")
    domain_name = domain.split(".")[0]

    # local part is exactly the full domain ("fly.io@fly.io")
    if local == domain:
        return True

    # local part is exactly the domain name ("fly@fly.io")
    if local == domain_name:
        return True

    # local part is the domain with dots ("fly.io@fly.io", already caught,
    # but also "example.com@example.com")
    if local.replace(".", "") == domain.replace(".", ""):
        return True

    return False


def _merge_contacts(all_results: list[dict]) -> list[dict]:
    by_email: dict[str, dict] = {}
    unnamed: list[dict] = []

    for c in all_results:
        email = c.get("email", "")
        if not email:
            unnamed.append({
                "email": "",
                "name": c.get("name", ""),
                "title": c.get("title", ""),
                "confidence": c.get("confidence", 0.2),
                "source": c.get("source", ""),
            })
            continue

        email_lower = email.lower()
        if email_lower in by_email:
            existing = by_email[email_lower]
            if c.get("confidence", 0) > existing["confidence"]:
                existing["confidence"] = c["confidence"]
            if c.get("name") and not existing["name"]:
                existing["name"] = c["name"]
            if c.get("title") and not existing["title"]:
                existing["title"] = c["title"]
            existing["source"] += f", {c.get('source', '')}"
        else:
            by_email[email_lower] = {
                "email": email_lower,
                "name": c.get("name", ""),
                "title": c.get("title", ""),
                "confidence": c.get("confidence", 0.3),
                "source": c.get("source", ""),
            }

    merged = [c for c in by_email.values() if c.get("email")]
    merged.sort(key=lambda x: x["confidence"], reverse=True)
    return merged


async def find_contacts(company_name: str, company_url: str) -> list[dict]:
    """
    Run all contact discovery methods for a company.
    Returns deduplicated list of contacts sorted by confidence.
    Each contact: {"email": str, "name": str, "title": str, "confidence": float, "source": str}
    """
    domain = get_domain_from_url(company_url)

    try:
        search_task = asyncio.create_task(search_contacts(company_name, domain))
        scrape_task = asyncio.create_task(scrape_company_contacts(company_url))
        search_results, scrape_results = await asyncio.gather(
            search_task, scrape_task, return_exceptions=True
        )
    except Exception:
        search_results = []
        scrape_results = []

    if isinstance(search_results, Exception):
        search_results = []
    if isinstance(scrape_results, Exception):
        scrape_results = []

    logger.info("[%s] Search found %d contacts, scrape found %d contacts",
                company_name, len(search_results), len(scrape_results))

    all_results = []

    for c in search_results:
        c.setdefault("confidence", 0.4)
        all_results.append(c)

    for c in scrape_results:
        c.setdefault("confidence", 0.5)
        all_results.append(c)

    generic_guesses = guess_emails(company_name, company_url)
    all_results.extend(generic_guesses)
    logger.debug("[%s] Generated %d generic email guesses", company_name, len(generic_guesses))

    named_contacts = [
        c for c in (list(search_results) + list(scrape_results))
        if c.get("name")
    ]
    for contact in named_contacts:
        name_guesses = guess_emails(company_name, company_url, contact["name"])
        for g in name_guesses:
            if contact.get("title"):
                g["title"] = contact["title"]
        all_results.extend(name_guesses)

    logger.debug("[%s] Generated guesses for %d named contacts", company_name, len(named_contacts))

    merged = _merge_contacts(all_results)

    # Filter out obviously bad email patterns
    merged = [c for c in merged if not _is_obviously_bad_email(c["email"])]

    # Cap candidates before expensive SMTP verification
    MAX_VERIFY = 20
    candidates = merged[:MAX_VERIFY]

    # SMTP verification: run concurrently with a semaphore to limit connections
    _smtp_sem = asyncio.Semaphore(8)

    async def _verify_one(contact):
        async with _smtp_sem:
            ok = await asyncio.to_thread(verify_email_exists, contact["email"])
            return contact if ok else None

    results = await asyncio.gather(*[_verify_one(c) for c in candidates])
    verified = [c for c in results if c is not None]

    logger.info("[%s] Final: %d verified contacts from %d candidates",
                company_name, len(verified), len(candidates))
    for c in verified[:5]:
        logger.debug("[%s]   %s (%s) — confidence=%.2f, source=%s",
                     company_name, c["email"], c.get("title", ""), c["confidence"], c["source"])

    return verified
