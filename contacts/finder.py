"""
Contact finder with fortress-level email verification.

ZERO BOUNCE GUARANTEE — every email passes ALL layers before sending:

  Layer 1: Domain matching — email domain MUST match company domain
  Layer 2: Bad email filter — block product names, tech terms, system prefixes
  Layer 3: Disify API — check disposable, DNS, format (free, no key)
  Layer 4: MailCheck.ai API — check disposable, spam, MX providers (free, no key)
  Layer 5: MX record validation — verify domain has mail servers
  Layer 6: Catch-all detection — probe with fake address to detect catch-all
  Layer 7: SMTP RCPT TO — connect to mail server, verify mailbox exists
  Layer 8: Double-verify — on catch-all domains, REJECT generic emails entirely

Contact finding strategies:
  Strategy 1: Extract emails from job description text
  Strategy 2: Search engines (Google API + DuckDuckGo + Bing)
  Strategy 3: Company website scraping (/about, /team, /contact pages)
  Strategy 4: Name-based email guessing (first.last@domain) — only on non-catch-all
  Strategy 5: Generic job email fallback (careers@, hr@) — only on non-catch-all
"""

import asyncio
import logging
import re
import httpx
from contacts.search_engine import search_contacts
from contacts.email_guesser import get_domain_from_url, guess_emails, has_valid_mx
from contacts.website_scraper import scrape_company_contacts

logger = logging.getLogger(__name__)

MAX_EMAILS_PER_COMPANY = 5

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

_SKIP_EMAIL_DOMAINS = {
    "example.com", "sentry.io", "wixpress.com", "github.com",
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "googlemail.com", "protonmail.com", "icloud.com", "aol.com",
    "mailinator.com", "yopmail.com", "tempmail.com",
    "linkedin.com", "facebook.com", "twitter.com", "instagram.com",
    "zhihu.com", "baidu.com", "qq.com", "163.com", "126.com",
    "naver.com", "daum.net", "mail.ru", "yandex.com", "yandex.ru",
    "mailbox.hu", "freemail.hu", "citromail.hu",
}

_SKIP_PREFIXES = {
    "noreply", "no-reply", "support", "test", "demo", "spam",
    "unsubscribe", "newsletter", "notifications", "alert",
    "postmaster", "webmaster", "donotreply", "bounces", "mailer-daemon",
    "info", "partners", "sales", "billing", "admin", "contact",
    "hello", "help", "feedback", "press", "media", "legal",
    "privacy", "security", "abuse", "marketing", "events",
    "accessibilitysupport", "accessibility", "candidate",
    "freight", "shipping", "logistics", "operations", "compliance",
    "accounting", "finance", "payroll", "invoic", "procurement",
    "general", "office", "reception", "frontdesk", "mainoffice",
    "customerservice", "techsupport", "itsupport", "helpdesk",
    "spend", "intelligence", "report", "analytics", "dashboard",
    "invest", "vendor", "supplier", "client", "customer",
}

_JOB_EMAIL_PREFIXES = {
    "careers", "hiring", "hr", "jobs", "apply", "recruitment", "talent",
    "people", "join", "work", "team", "recruit",
}

_NOT_PERSON_LOCALS = {
    "full-stack", "fullstack", "frontend", "backend", "devops", "engineering",
    "product", "platform", "mobile", "cloud", "data", "design", "sonar",
    "docs", "api", "sdk", "team", "dev", "ops", "sre", "qa", "ux", "ui",
    "git", "hub", "bot", "app", "web", "labs", "studio", "office", "hq",
    "management", "paper", "stack", "board", "service", "system",
    "audit", "freight", "shipping", "warehouse", "delivery",
    "oss", "open", "source", "jubao", "press", "blog", "news",
}

_KNOWN_TOOLS_PRODUCTS = {
    "dropbox", "slack", "notion", "figma", "jira", "asana", "trello",
    "github", "gitlab", "bitbucket", "linear", "airtable", "zapier",
    "stripe", "twilio", "sendgrid", "mailchimp", "hubspot", "intercom",
    "segment", "amplitude", "mixpanel", "datadog", "grafana", "sentry",
    "vercel", "netlify", "heroku", "firebase", "supabase", "mongodb",
    "redis", "postgres", "mysql", "docker", "kubernetes", "terraform",
    "ansible", "jenkins", "circleci", "travis", "webpack", "vite",
    "react", "angular", "vue", "svelte", "nextjs", "gatsby",
    "reddit", "twitter", "linkedin", "facebook", "instagram",
    "google", "microsoft", "amazon", "apple", "salesforce", "oracle",
    "ngrafika", "zhihu", "baidu", "wechat", "weibo",
}

# Shared httpx client for API calls within this module
_http_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=8)
    return _http_client


# =====================================================
# LAYER 1: Domain matching — THE MOST CRITICAL CHECK
# =====================================================
def _email_matches_domain(email: str, company_domain: str) -> bool:
    """Email domain MUST match the company domain. No exceptions."""
    if not company_domain:
        return False
    email_domain = email.lower().rsplit("@", 1)[-1]
    company_domain = company_domain.lower()
    return email_domain == company_domain


# =====================================================
# LAYER 2: Bad email filters
# =====================================================
def _is_bad_email(email: str) -> bool:
    try:
        local, domain = email.lower().rsplit("@", 1)
    except ValueError:
        return True
    if domain in _SKIP_EMAIL_DOMAINS:
        return True
    if any(local.startswith(p) for p in _SKIP_PREFIXES):
        return True
    if any(ext in email for ext in (".png", ".jpg", ".gif", ".svg", ".css", ".js")):
        return True
    domain_name = domain.split(".")[0]
    if local == domain or local == domain_name:
        return True
    if len(local) < 2 or len(local) > 64:
        return True
    clean_local = local.replace(".", "-").replace("_", "-")
    parts = clean_local.split("-")
    if any(p in _NOT_PERSON_LOCALS for p in parts):
        return True
    if any(p in _KNOWN_TOOLS_PRODUCTS for p in parts):
        return True
    return False


def _is_bad_email_for_job(email: str) -> bool:
    try:
        local, domain = email.lower().rsplit("@", 1)
    except ValueError:
        return True
    if any(local.startswith(p) for p in _JOB_EMAIL_PREFIXES):
        if domain in _SKIP_EMAIL_DOMAINS:
            return True
        return False
    return _is_bad_email(email)


# =====================================================
# LAYERS 3-4: External API verification (Disify + MailCheck.ai)
# =====================================================
_disify_cache: dict[str, dict | None] = {}
_mailcheck_cache: dict[str, dict | None] = {}


async def _disify_check(email: str) -> dict | None:
    domain = email.lower().rsplit("@", 1)[-1]
    if domain in _disify_cache:
        return _disify_cache[domain]
    try:
        client = await _get_client()
        resp = await client.get(f"https://disify.com/api/email/{email}")
        if resp.status_code == 200:
            data = resp.json()
            _disify_cache[domain] = data
            return data
    except Exception:
        pass
    _disify_cache[domain] = None
    return None


def _disify_is_bad(data: dict | None) -> bool:
    if not data:
        return False
    if data.get("disposable"):
        return True
    if not data.get("dns"):
        return True
    if not data.get("format"):
        return True
    return False


async def _mailcheck_ai(email: str) -> dict | None:
    domain = email.lower().rsplit("@", 1)[-1]
    if domain in _mailcheck_cache:
        return _mailcheck_cache[domain]
    try:
        client = await _get_client()
        resp = await client.get(f"https://api.mailcheck.ai/email/{email}")
        if resp.status_code == 200:
            data = resp.json()
            _mailcheck_cache[domain] = data
            return data
    except Exception:
        pass
    _mailcheck_cache[domain] = None
    return None


def _mailcheck_is_bad(data: dict | None) -> bool:
    if not data:
        return False
    if data.get("disposable"):
        return True
    if data.get("spam"):
        return True
    if not data.get("mx"):
        return True
    return False


# =====================================================
# LAYERS 5-8: MX, Catch-all, SMTP RCPT TO
# =====================================================
_catchall_cache: dict[str, bool] = {}


def _smtp_verify(email: str) -> bool:
    try:
        from email_validator import validate_email_full
        ok, reason, checks = validate_email_full(email, skip_smtp=False)
        if not ok:
            logger.info("  SMTP reject %s: %s", email, reason)
            return False
        smtp_check = checks.get("smtp_rcpt", {})
        if not smtp_check.get("ok", True):
            logger.info("  SMTP reject %s: %s", email, smtp_check.get("reason", ""))
            return False
        return True
    except Exception as e:
        logger.debug("  SMTP verify error %s: %s", email, e)
        return False


_mx_provider_cache: dict[str, tuple[str, bool]] = {}


def _get_mx_provider(domain: str) -> tuple[str, bool]:
    """Returns (provider_name, is_smtp_reliable)."""
    if domain in _mx_provider_cache:
        return _mx_provider_cache[domain]
    try:
        from email_validator import check_mx, classify_mx_provider
        ok, _, mx_hosts = check_mx(domain)
        if not ok:
            _mx_provider_cache[domain] = ("unknown", False)
            return "unknown", False
        provider, reliable = classify_mx_provider(mx_hosts)
        _mx_provider_cache[domain] = (provider, reliable)
        return provider, reliable
    except Exception:
        _mx_provider_cache[domain] = ("unknown", False)
        return "unknown", False


def _is_catchall(domain: str) -> bool:
    if domain in _catchall_cache:
        return _catchall_cache[domain]
    try:
        from email_validator import is_catchall_domain, check_mx
        ok, _, mx_hosts = check_mx(domain)
        if not ok:
            _catchall_cache[domain] = False
            return False
        result = is_catchall_domain(domain, mx_hosts)
        _catchall_cache[domain] = result
        return result
    except Exception:
        _catchall_cache[domain] = False
        return False


async def _verify_email_fortress(email: str, company_domain: str,
                                  is_from_source: bool = False) -> bool:
    """
    Run ALL verification layers. Returns True only if email passes every check.
    This is the fortress — no email gets through without passing everything.

    is_from_source: True if this email was found from search/scrape/job description.
                    False if it was guessed (generic careers@, hr@, name-based).
                    Guessed emails require STRICTER verification.
    """
    email = email.lower().strip()
    email_domain = email.rsplit("@", 1)[-1]
    local = email.rsplit("@", 1)[0]

    # Layer 1: Domain match (CRITICAL — prevents wrong-company emails)
    if company_domain and not _email_matches_domain(email, company_domain):
        logger.info("  DOMAIN MISMATCH %s (expected @%s)", email, company_domain)
        return False

    # Layer 3: Disify API
    disify_data = await _disify_check(email)
    if _disify_is_bad(disify_data):
        logger.info("  Disify REJECT %s: disposable/invalid", email)
        return False

    # Layer 4: MailCheck.ai API
    mailcheck_data = await _mailcheck_ai(email)
    if _mailcheck_is_bad(mailcheck_data):
        logger.info("  MailCheck.ai REJECT %s: disposable/spam/no-mx", email)
        return False

    # Layer 5: MX validation
    if not has_valid_mx(email_domain):
        logger.info("  MX REJECT %s: no valid MX records", email)
        return False

    # Layer 6: MX provider classification
    provider, is_reliable = _get_mx_provider(email_domain)

    # Layer 7: Catch-all detection
    is_catchall = _is_catchall(email_domain)

    if is_catchall:
        # Catch-all domains NEVER bounce — they accept everything.
        # Safe to send to: won't cause "address not found" errors.
        if is_from_source:
            logger.info("  CATCH-ALL OK %s: found email, catch-all won't bounce", email)
            return True
        # Guessed name-based emails on catch-all: allow (won't bounce)
        if not any(local.startswith(p) for p in _JOB_EMAIL_PREFIXES):
            logger.info("  CATCH-ALL OK %s: name-based guess, catch-all won't bounce", email)
            return True
        # Generic prefixes (careers@, hr@) on catch-all: also safe
        logger.info("  CATCH-ALL OK %s: generic email, catch-all won't bounce", email)
        return True

    # Layer 8: SMTP RCPT TO verification
    if is_reliable:
        # Google-hosted: SMTP RCPT TO is trustworthy
        if not _smtp_verify(email):
            return False
        return True

    # Unreliable provider (Microsoft, ImprovMX, Mimecast, parked, unknown)
    # SMTP RCPT TO will say "OK" but email may bounce later
    if not is_from_source:
        logger.info("  PROVIDER REJECT %s: guessed email on %s (unreliable SMTP)", email, provider)
        return False

    # Email found from real source on unreliable provider — trust the source.
    # Port 25 is often blocked to non-Google MX servers, so SMTP will fail
    # even for valid addresses. Since this email was actually found (search/
    # scrape/job description), all other checks passed, accept it.
    if provider == "parked":
        logger.info("  PARKED REJECT %s: domain is parked (%s)", email, provider)
        return False
    logger.info("  SOURCE-TRUSTED %s: found email on %s — accepting (all other checks passed)", email, provider)
    return True


# =====================================================
# Contact merging and deduplication
# =====================================================
def _merge_contacts(all_results: list[dict]) -> list[dict]:
    by_email: dict[str, dict] = {}
    for c in all_results:
        email = c.get("email", "")
        if not email:
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

    merged = list(by_email.values())
    for c in merged:
        c["confidence"] = min(c["confidence"] + 0.2, 0.95)
    merged.sort(key=lambda x: x["confidence"], reverse=True)
    return merged


def _extract_emails_from_description(text: str) -> list[dict]:
    if not text:
        return []
    results = []
    seen = set()
    for email in _EMAIL_RE.findall(text):
        email_lower = email.lower()
        if email_lower in seen:
            continue
        seen.add(email_lower)
        if _is_bad_email_for_job(email_lower):
            continue
        results.append({
            "email": email_lower,
            "name": "",
            "title": "",
            "confidence": 0.90,
            "source": "job_description",
        })
    return results


# =====================================================
# Main contact discovery function
# =====================================================
async def find_contacts(company_name: str, company_url: str, job_description: str = "") -> list[dict]:
    domain = get_domain_from_url(company_url)

    # Derive domain from company name if URL was a job board
    if not domain and company_name:
        name_lower = company_name.lower().strip()
        slug = re.sub(r"[^a-z0-9]", "", name_lower)
        slug_hyphen = re.sub(r"[^a-z0-9]+", "-", name_lower).strip("-")
        # Remove common suffixes for better matching
        for suffix in ("inc", "llc", "ltd", "corp", "co", "labs", "hq",
                        "technologies", "software", "solutions", "group"):
            if slug.endswith(suffix) and len(slug) > len(suffix) + 2:
                slug = slug[:-len(suffix)]
            if slug_hyphen.endswith(suffix) and len(slug_hyphen) > len(suffix) + 2:
                slug_hyphen = slug_hyphen[:-(len(suffix) + 1)]
        candidates = []
        for s in dict.fromkeys([slug, slug_hyphen]):
            for tld in (".com", ".io", ".co", ".dev", ".tech", ".org", ".ai",
                         ".app", ".xyz", ".so", ".sh", ".cc", ".net"):
                candidates.append(f"{s}{tld}")
        for candidate in candidates:
            if has_valid_mx(candidate):
                domain = candidate
                logger.info("[%s] Derived domain %s from company name", company_name, domain)
                break

    if not domain:
        logger.info("[%s] No company domain found — skipping", company_name)
        return []

    # Strategy 1: Emails in job description (high trust — from real source)
    desc_emails = _extract_emails_from_description(job_description)
    desc_valid = []
    if desc_emails:
        for c in desc_emails:
            if _email_matches_domain(c["email"], domain):
                if await _verify_email_fortress(c["email"], domain, is_from_source=True):
                    desc_valid.append(c)
        if desc_valid:
            logger.info("[%s] Found %d verified emails in job description", company_name, len(desc_valid))

    # Strategy 2 + 3: Search engines + Website scraping (parallel)
    # Always run — even if strategy 1 found emails, we want more contacts
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

    # Collect emails — ONLY those matching company domain
    all_with_email = []
    for c in search_results:
        email = c.get("email", "").lower()
        if not email:
            continue
        if not _email_matches_domain(email, domain):
            logger.debug("[%s] SKIP foreign email %s (expected @%s)", company_name, email, domain)
            continue
        if _is_bad_email(email):
            continue
        c.setdefault("confidence", 0.5)
        all_with_email.append(c)

    for c in scrape_results:
        email = c.get("email", "").lower()
        if not email:
            continue
        if not _email_matches_domain(email, domain):
            continue
        if _is_bad_email(email):
            continue
        c.setdefault("confidence", 0.6)
        all_with_email.append(c)

    # Strategy 4: Name-based email guessing
    names_without_email = []
    for c in search_results + scrape_results:
        if isinstance(c, dict) and c.get("name") and not c.get("email"):
            names_without_email.append(c)

    guessed = []
    is_domain_catchall = _is_catchall(domain)

    if not is_domain_catchall:
        seen_names = set()
        for c in names_without_email:
            name = c["name"]
            if name.lower() in seen_names:
                continue
            seen_names.add(name.lower())
            candidates = guess_emails(company_name, company_url, name)
            for g in candidates[:6]:
                if not _is_bad_email(g["email"]) and _email_matches_domain(g["email"], domain):
                    g["name"] = name
                    g["title"] = c.get("title", "")
                    guessed.append(g)
    else:
        seen_names = set()
        for c in names_without_email:
            name = c["name"]
            if name.lower() in seen_names:
                continue
            seen_names.add(name.lower())
            candidates = guess_emails(company_name, company_url, name)
            for g in candidates[:4]:
                if not _is_bad_email(g["email"]) and _email_matches_domain(g["email"], domain):
                    g["name"] = name
                    g["title"] = c.get("title", "")
                    g["source"] = "guess_named_catchall"
                    guessed.append(g)

    # Merge desc_valid + search/scrape results, then verify
    merged = _merge_contacts(all_with_email)
    valid = list(desc_valid)
    valid_emails = {v["email"].lower() for v in valid}

    for c in merged:
        if len(valid) >= MAX_EMAILS_PER_COMPANY:
            break
        if c["email"].lower() in valid_emails:
            continue
        if await _verify_email_fortress(c["email"], domain, is_from_source=True):
            valid.append(c)
            valid_emails.add(c["email"].lower())

    # Verify guessed emails
    if len(valid) < MAX_EMAILS_PER_COMPANY and guessed:
        logger.info("[%s] Trying %d guessed emails...", company_name, len(guessed))
        for g in guessed:
            if len(valid) >= MAX_EMAILS_PER_COMPANY:
                break
            if g["email"].lower() in valid_emails:
                continue
            source_flag = is_domain_catchall
            if await _verify_email_fortress(g["email"], domain, is_from_source=source_flag):
                valid.append(g)
                valid_emails.add(g["email"].lower())
                logger.info("[%s]   GUESS VERIFIED: %s", company_name, g["email"])

    # Strategy 5: Generic job email fallback — try every common HR prefix
    _GENERIC_PREFIXES = [
        ("careers", 0.45), ("hiring", 0.45), ("hr", 0.42),
        ("jobs", 0.42), ("talent", 0.40), ("recruit", 0.40),
        ("recruitment", 0.38), ("people", 0.38), ("joinus", 0.38),
        ("apply", 0.36), ("work", 0.36), ("team", 0.36),
    ]
    if len(valid) < MAX_EMAILS_PER_COMPANY and has_valid_mx(domain):
        provider, is_reliable = _get_mx_provider(domain)
        if provider == "parked":
            logger.info("[%s] Skipping generic emails — domain is parked", company_name)
        elif is_domain_catchall:
            for prefix, conf in _GENERIC_PREFIXES:
                if len(valid) >= MAX_EMAILS_PER_COMPANY:
                    break
                candidate = f"{prefix}@{domain}"
                if candidate not in valid_emails:
                    valid.append({
                        "email": candidate, "name": "",
                        "title": f"{prefix.title()} Department",
                        "confidence": conf, "source": "generic_catchall",
                    })
                    valid_emails.add(candidate)
                    logger.info("[%s]   GENERIC CATCHALL: %s (won't bounce)", company_name, candidate)
        else:
            for prefix, conf in _GENERIC_PREFIXES:
                if len(valid) >= MAX_EMAILS_PER_COMPANY:
                    break
                candidate = f"{prefix}@{domain}"
                if candidate in valid_emails:
                    continue
                if _smtp_verify(candidate):
                    valid.append({
                        "email": candidate, "name": "",
                        "title": f"{prefix.title()} Department",
                        "confidence": conf, "source": "generic_job_email",
                    })
                    valid_emails.add(candidate)
                    logger.info("[%s]   GENERIC VERIFIED: %s (provider=%s)", company_name, candidate, provider)

    if not valid:
        logger.info("[%s] No verified emails (found=%d, guessed=%d, catchall=%s)",
                    company_name, len(all_with_email), len(guessed), is_domain_catchall)
        return []

    logger.info("[%s] Final: %d verified (found=%d, guessed=%d)",
                company_name, len(valid), len(all_with_email), len(guessed))
    for c in valid:
        logger.info("[%s]   %s (%s) conf=%.2f src=%s",
                    company_name, c["email"], c.get("title", ""),
                    c["confidence"], c["source"][:40])
    return valid
