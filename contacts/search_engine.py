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
    "about", "accept", "access", "account", "action", "active", "admin",
    "after", "also", "america", "android", "angular", "annual", "another",
    "apple", "apply", "approach", "area", "article", "available",
    "back", "based", "been", "before", "being", "best", "between",
    "browser", "build", "business", "button",
    "call", "came", "canada", "career", "center", "change", "check",
    "chrome", "city", "click", "close", "cloud", "code", "come",
    "community", "company", "connect", "contact", "continue", "cookie",
    "corporate", "could", "country", "create", "current", "customer",
    "daily", "dashboard", "data", "date", "deep", "demo", "department",
    "design", "desktop", "detail", "developer", "digital", "director",
    "discover", "does", "down", "download", "drive",
    "each", "easy", "email", "employee", "enable", "energy", "engineer",
    "enterprise", "error", "europe", "even", "event", "every", "example",
    "experience", "explore", "external",
    "facebook", "fast", "feature", "feedback", "file", "filter", "finance",
    "financial", "find", "firefox", "first", "follow", "form", "found",
    "free", "from", "full", "future",
    "general", "generate", "getting", "give", "global", "good", "great",
    "green", "group", "grow", "growth", "guide",
    "have", "help", "here", "high", "hiring", "home", "host", "hour", "house",
    "human",
    "idea", "impact", "improve", "include", "india", "industry",
    "information", "infrastructure", "inside", "instagram", "internal",
    "into", "issue",
    "jobs", "join", "just",
    "keep", "key",
    "large", "last", "latest", "launch", "lead", "leading", "learn",
    "legal", "level", "life", "like", "link", "linkedin", "linux",
    "list", "live", "loading", "local", "location", "login", "london",
    "long", "look",
    "made", "main", "make", "manage", "manager", "many", "market",
    "marketing", "match", "media", "meet", "member", "message",
    "million", "mobile", "model", "month", "more", "most", "move",
    "much", "must",
    "name", "need", "network", "never", "news", "next", "north",
    "note", "number",
    "offer", "office", "often", "only", "open", "operation", "option",
    "order", "other", "over", "overview", "own",
    "page", "paid", "part", "partner", "past", "path", "people",
    "percent", "performance", "person", "phone", "place", "plan",
    "platform", "play", "please", "plus", "point", "policy", "post",
    "power", "practice", "press", "prev", "price", "privacy",
    "process", "product", "professional", "profile", "program",
    "project", "public", "push",
    "quality", "question", "quick",
    "range", "rate", "reach", "read", "ready", "real", "recent",
    "related", "release", "remote", "report", "require", "research",
    "resource", "result", "review", "right", "role", "round", "rule",
    "safe", "sale", "sales", "same", "save", "scale", "schedule",
    "school", "search", "security", "senior", "series", "server",
    "service", "share", "should", "show", "side", "sign", "simple",
    "site", "skill", "small", "social", "software", "solution",
    "some", "source", "south", "space", "special", "spotify",
    "stack", "staff", "stage", "standard", "start", "state", "states",
    "status", "stay", "step", "still", "stop", "store", "strong",
    "student", "submit", "success", "such", "suite", "super", "support",
    "sure", "system",
    "take", "talk", "team", "tech", "technology", "term", "test",
    "text", "than", "that", "their", "them", "then", "there", "these",
    "they", "thing", "think", "this", "those", "through", "time",
    "title", "today", "tool", "total", "track", "trade", "training",
    "travel", "true", "turn", "type",
    "under", "united", "university", "update", "upper", "used", "user",
    "using",
    "value", "very", "video", "view", "visit",
    "want", "watch", "water", "week", "well", "west", "what", "when",
    "where", "which", "while", "white", "whole", "wide", "will",
    "windows", "with", "within", "without", "word", "work", "working",
    "world", "would", "write",
    "year", "york", "your",
    # common non-person capitalized pairs from search snippets
    "customer", "onsite", "hybrid", "junior", "overflow",
}

SKIP_EMAIL_DOMAINS = {
    "example.com", "sentry.io", "wixpress.com", "github.com",
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "googlemail.com", "protonmail.com", "icloud.com", "aol.com",
}

SKIP_EMAIL_PREFIXES = {
    "noreply", "no-reply", "support", "test", "demo", "spam",
    "unsubscribe", "newsletter", "feedback", "abuse", "postmaster",
    "webmaster", "admin", "root", "mailer-daemon", "donotreply",
    "notifications", "alert", "info+", "bounces", "devnull",
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
    if any(len(p) < 3 for p in parts):
        return False
    if any(p == p.upper() for p in name.split()):
        return False
    return True


def _is_valid_email(email: str) -> bool:
    lower = email.lower()
    local = lower.split("@")[0]
    domain = lower.split("@")[-1]
    if domain in SKIP_EMAIL_DOMAINS:
        return False
    if any(local.startswith(p) for p in SKIP_EMAIL_PREFIXES):
        return False
    if any(ext in lower for ext in [".png", ".jpg", ".gif", ".svg", ".css", ".js"]):
        return False
    return True


def _extract_contacts_from_text(text: str, source: str) -> list[dict]:
    contacts = []
    emails = EMAIL_REGEX.findall(text)
    title_matches = TITLE_PATTERNS.findall(text)
    name_matches = NAME_PATTERN.findall(text)

    for email in emails:
        if not _is_valid_email(email):
            continue
        contacts.append({
            "name": "",
            "title": "",
            "email": email,
            "source": source,
        })

    # Only extract names that appear near a title — otherwise they're garbage
    if title_matches:
        for name in name_matches:
            if not _is_real_name(name):
                continue
            contacts.append({
                "name": name,
                "title": title_matches[0],
                "email": "",
                "source": source,
            })

    return contacts


async def _fetch_page_emails(client: httpx.AsyncClient, url: str) -> list[dict]:
    try:
        resp = await client.get(url, headers=_random_headers(), timeout=10)
        if resp.status_code != 200:
            return []
        text = resp.text[:50000]
        return _extract_contacts_from_text(text, f"page_scrape:{url[:60]}")
    except Exception:
        return []


async def _search_google_api(client: httpx.AsyncClient, query: str) -> list[dict]:
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
                promising_urls = []
                for item in data.get("items", []):
                    link = item.get("link", "")
                    title = item.get("title", "").lower()
                    if any(kw in link.lower() + " " + title for kw in ["team", "about", "contact", "people", "leadership", "founder"]):
                        promising_urls.append(link)
                for url in promising_urls[:2]:
                    page_contacts = await _fetch_page_emails(client, url)
                    contacts.extend(page_contacts)
                return contacts
            if resp.status_code == 429:
                continue
        except Exception:
            pass
    return []


async def _search_ddg(client: httpx.AsyncClient, query: str) -> list[dict]:
    resp = await client.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers=_random_headers(),
        timeout=10,
    )
    if resp.status_code != 200:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    contacts = []
    for result in soup.select(".result__snippet, .result__title, .result__body"):
        text = result.get_text(separator=" ", strip=True)
        contacts.extend(_extract_contacts_from_text(text, "duckduckgo"))
    return contacts


async def _search_bing(client: httpx.AsyncClient, query: str) -> list[dict]:
    resp = await client.get(
        "https://www.bing.com/search",
        params={"q": query},
        headers=_random_headers(),
        timeout=10,
    )
    if resp.status_code != 200:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    contacts = []
    for result in soup.select(".b_algo"):
        text = result.get_text(separator=" ", strip=True)
        contacts.extend(_extract_contacts_from_text(text, "bing"))
    return contacts


async def _search_all_engines(client: httpx.AsyncClient, query: str) -> list[dict]:
    results = await asyncio.gather(
        _search_ddg(client, query),
        _search_bing(client, query),
        return_exceptions=True,
    )
    all_results = []
    for r in results:
        if isinstance(r, list):
            all_results.extend(r)
    return all_results


async def _run_query(client: httpx.AsyncClient, query: str, use_google_api: bool, query_idx: int) -> list[dict]:
    results = []
    if use_google_api and query_idx < 3:
        google_results = await _search_google_api(client, query)
        results.extend(google_results)
    engine_results = await _search_all_engines(client, query)
    results.extend(engine_results)
    return results


async def search_contacts(company_name: str, domain: str) -> list[dict]:
    queries = [
        f'"{company_name}" HR email hiring',
        f'"{company_name}" CTO OR CEO email',
        f'"{company_name}" careers OR talent email',
    ]
    if domain:
        queries.append(f'"@{domain}" hiring OR HR OR recruiter')
        queries.append(f'site:{domain} "@{domain}" team OR about OR contact')

    all_contacts = []
    use_google_api = bool(GOOGLE_API_KEYS) and bool(GOOGLE_CSE_ID)

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            batch_results = await asyncio.gather(
                *[_run_query(client, q, use_google_api, i) for i, q in enumerate(queries)],
                return_exceptions=True,
            )
            for r in batch_results:
                if isinstance(r, list):
                    all_contacts.extend(r)
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
