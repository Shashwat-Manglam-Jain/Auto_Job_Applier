import httpx
import asyncio
import random
import re
import logging
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

TITLE_PATTERNS = re.compile(
    r"\b(CTO|CEO|CFO|COO|CPO|CMO|VP\s+(?:of\s+)?Engineering|"
    r"Head\s+of\s+(?:Engineering|Talent|People|HR|Recruiting)|"
    r"HR\s+(?:Manager|Director|Lead)|Hiring\s+Manager|"
    r"Talent\s+(?:Acquisition|Manager|Lead)|"
    r"(?:Co-?)?Founder|Director\s+of\s+(?:Engineering|HR|People|Talent)|"
    r"Chief\s+(?:Technology|Executive|People)\s+Officer|"
    r"Recruiter|People\s+Operations)\b",
    re.IGNORECASE,
)

NAME_PATTERN = re.compile(r"\b([A-Z][a-z]{2,15} [A-Z][a-z]{2,15})\b")

NON_NAME_WORDS = {
    "browser", "company", "login", "page", "search", "google", "bing",
    "click", "here", "sign", "view", "more", "read", "open", "close",
    "home", "about", "contact", "privacy", "terms", "cookie", "accept",
    "loading", "error", "next", "prev", "back", "submit", "download",
    "remote", "hybrid", "onsite", "senior", "junior", "lead", "staff",
    "engineer", "developer", "manager", "director", "software", "data",
    "product", "design", "sales", "marketing", "finance", "legal",
    "apply", "save", "share", "report", "jobs", "hiring", "career",
    "india", "united", "states", "america", "europe", "london", "york",
    "customer", "support", "service", "project", "platform", "system",
    "digital", "global", "world", "today", "this", "that", "with",
    "mobile", "desktop", "windows", "linux", "chrome", "firefox",
    "key", "value", "link", "site", "web", "app", "tech", "full",
    "time", "part", "work", "working", "stack", "overflow",
}

from config import GOOGLE_API_KEYS, GOOGLE_CSE_ID

_google_key_idx = 0


def _random_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _is_real_name(name: str) -> bool:
    parts = name.lower().split()
    if any(p in NON_NAME_WORDS for p in parts):
        return False
    if any(len(p) < 2 for p in parts):
        return False
    if any(p == p.upper() for p in name.split()):
        return False
    return True


def _extract_contacts_from_text(text: str, source: str) -> list[dict]:
    contacts = []
    emails = EMAIL_REGEX.findall(text)
    title_matches = TITLE_PATTERNS.findall(text)
    name_matches = NAME_PATTERN.findall(text)

    for email in emails:
        if any(skip in email.lower() for skip in [
            "example.com", "sentry.io", "wixpress", "noreply",
            "no-reply", "support@", "test@", "demo@", "spam",
            "unsubscribe", "newsletter", "feedback@", "abuse@",
            "postmaster@", "webmaster@", "admin@", "root@",
            ".png", ".jpg", ".gif", "sentry", "github.com",
            "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
        ]):
            continue
        contacts.append({
            "name": "",
            "title": "",
            "email": email,
            "source": source,
        })

    for name in name_matches:
        if not _is_real_name(name):
            continue
        title = title_matches[0] if title_matches else ""
        contacts.append({
            "name": name,
            "title": title,
            "email": "",
            "source": source,
        })

    return contacts


async def _fetch_page_emails(client: httpx.AsyncClient, url: str) -> list[dict]:
    """Fetch a URL and extract any email addresses from the full page text."""
    try:
        resp = await client.get(url, headers=_random_headers(), timeout=10)
        if resp.status_code != 200:
            return []
        text = resp.text[:50000]  # Limit to first 50KB
        return _extract_contacts_from_text(text, f"page_scrape:{url[:60]}")
    except Exception:
        return []


async def _search_google_api(client: httpx.AsyncClient, query: str) -> list[dict]:
    """Use Google Custom Search JSON API, round-robin across all keys."""
    global _google_key_idx
    if not GOOGLE_API_KEYS or not GOOGLE_CSE_ID:
        return []
    for attempt in range(len(GOOGLE_API_KEYS)):
        idx = (_google_key_idx + attempt) % len(GOOGLE_API_KEYS)
        key = GOOGLE_API_KEYS[idx]
        try:
            resp = await client.get(
                "https://www.googleapis.com/customsearch/v1",
                params={"key": key, "cx": GOOGLE_CSE_ID, "q": query, "num": 10},
                timeout=15,
            )
            if resp.status_code == 200:
                _google_key_idx = (idx + 1) % len(GOOGLE_API_KEYS)
                data = resp.json()
                contacts = []
                for item in data.get("items", []):
                    text = f"{item.get('title', '')} {item.get('snippet', '')} {item.get('link', '')}"
                    contacts.extend(_extract_contacts_from_text(text, "google_api"))
                # Fetch promising pages for deeper email extraction
                promising_urls = []
                for item in data.get("items", []):
                    link = item.get("link", "")
                    title = item.get("title", "").lower()
                    if any(kw in link.lower() + " " + title for kw in ["team", "about", "contact", "people", "leadership", "founder", "management"]):
                        promising_urls.append(link)
                for url in promising_urls[:3]:
                    try:
                        page_contacts = await _fetch_page_emails(client, url)
                        contacts.extend(page_contacts)
                    except Exception:
                        pass
                return contacts
            if resp.status_code == 429:
                logger.debug("Google key #%d rate limited, rotating", idx + 1)
                continue
            logger.debug("Google key #%d returned %d", idx + 1, resp.status_code)
        except Exception as e:
            logger.debug("Google key #%d failed: %s", idx + 1, e)
    return []


async def _search_google_scrape(client: httpx.AsyncClient, query: str) -> list[dict]:
    """Scrape Google search results directly as fallback."""
    resp = await client.get(
        "https://www.google.com/search",
        params={"q": query, "num": 10, "hl": "en"},
        headers=_random_headers(),
        timeout=15,
    )
    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    contacts = []
    for div in soup.find_all("div", class_="BNeawe"):
        text = div.get_text(separator=" ", strip=True)
        contacts.extend(_extract_contacts_from_text(text, "google"))
    for span in soup.find_all("span"):
        text = span.get_text(separator=" ", strip=True)
        found = _extract_contacts_from_text(text, "google")
        contacts.extend(found)
    return contacts


async def _search_ddg(client: httpx.AsyncClient, query: str) -> list[dict]:
    resp = await client.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers=_random_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    contacts = []
    for result in soup.select(".result__snippet, .result__title, .result__body"):
        text = result.get_text(separator=" ", strip=True)
        contacts.extend(_extract_contacts_from_text(text, "duckduckgo"))
    return contacts


async def _search_ddg_api(client: httpx.AsyncClient, query: str) -> list[dict]:
    """Try DuckDuckGo instant answer API for additional results."""
    try:
        resp = await client.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            headers=_random_headers(),
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        contacts = []
        # Check abstract, answer, and related topics
        for field in ["Abstract", "Answer"]:
            text = data.get(field, "")
            if text:
                contacts.extend(_extract_contacts_from_text(text, "ddg_api"))
        for topic in data.get("RelatedTopics", []):
            text = topic.get("Text", "")
            if text:
                contacts.extend(_extract_contacts_from_text(text, "ddg_api"))
        return contacts
    except Exception:
        return []


async def _search_bing(client: httpx.AsyncClient, query: str) -> list[dict]:
    resp = await client.get(
        "https://www.bing.com/search",
        params={"q": query},
        headers=_random_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    contacts = []
    for result in soup.select(".b_algo"):
        text = result.get_text(separator=" ", strip=True)
        contacts.extend(_extract_contacts_from_text(text, "bing"))
    return contacts


async def _search_all_engines(client: httpx.AsyncClient, query: str) -> list[dict]:
    """Try DDG and Bing for every query, merge results. Fall back to Google scrape if both fail."""
    all_results = []

    # Always try DDG first (most reliable)
    try:
        ddg_results = await _search_ddg(client, query)
        all_results.extend(ddg_results)
    except Exception as e:
        logger.debug("DDG search failed: %s", str(e)[:50])

    # Also try DDG instant answer API
    try:
        ddg_api_results = await _search_ddg_api(client, query)
        all_results.extend(ddg_api_results)
    except Exception as e:
        logger.debug("DDG API search failed: %s", str(e)[:50])

    # Also try Bing for extra results
    try:
        bing_results = await _search_bing(client, query)
        all_results.extend(bing_results)
    except Exception as e:
        logger.debug("Bing search failed: %s", str(e)[:50])

    # Fall back to Google scrape only if both failed
    if not all_results:
        try:
            google_results = await _search_google_scrape(client, query)
            all_results.extend(google_results)
        except Exception as e:
            logger.debug("Google scrape failed: %s", str(e)[:50])

    return all_results


async def search_contacts(company_name: str, domain: str) -> list[dict]:
    queries = [
        f'"{company_name}" HR email hiring manager',
        f'"{company_name}" CTO OR CEO email contact',
        f'"@{domain}" hiring OR HR OR recruiter',
        f'site:linkedin.com/in "{company_name}" CTO OR "head of engineering"',
        f'"{company_name}" careers team email',
        f'"{company_name}" engineering manager email',
        f'"{company_name}" "people operations" OR "talent acquisition" email',
        f'site:twitter.com OR site:x.com "{company_name}" CTO OR CEO',
    ]

    all_contacts = []
    use_google_api = bool(GOOGLE_API_KEYS) and bool(GOOGLE_CSE_ID)

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            for i, query in enumerate(queries):
                try:
                    results = []
                    if use_google_api and i < 3:
                        google_results = await _search_google_api(client, query)
                        results.extend(google_results)
                    engine_results = await _search_all_engines(client, query)
                    results.extend(engine_results)
                    all_contacts.extend(results)
                except Exception as e:
                    logger.debug("All searches failed for: %s", query[:50])

                await asyncio.sleep(random.uniform(1.5, 3))
    except Exception:
        logger.warning("Contact search completely failed for %s", company_name)
        return []

    seen = set()
    deduped = []
    for c in all_contacts:
        key = (c["email"].lower() if c["email"] else "") or c["name"]
        if key and key not in seen:
            seen.add(key)
            deduped.append(c)

    return deduped
