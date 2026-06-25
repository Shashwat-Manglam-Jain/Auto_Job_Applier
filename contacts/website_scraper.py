import httpx
import re
import json
import logging
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

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

NAME_PATTERN = re.compile(r"\b([A-Z][a-z]+ [A-Z][a-z]+)\b")

CONTACT_PATHS = [
    "/about", "/about-us", "/team", "/people",
    "/contact", "/contact-us",
    "/careers", "/jobs", "/company", "/leadership",
    "/our-team", "/management",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SOCIAL_PATTERNS = {
    "twitter": re.compile(r'https?://(?:www\.)?(?:twitter|x)\.com/([a-zA-Z0-9_]+)', re.IGNORECASE),
    "linkedin": re.compile(r'https?://(?:www\.)?linkedin\.com/in/([a-zA-Z0-9_-]+)', re.IGNORECASE),
}


def _extract_jsonld_contacts(soup: BeautifulSoup) -> list[dict]:
    contacts = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            items = data if isinstance(data, list) else [data]
            for item in items:
                email = item.get("email", "")
                name = item.get("name", "")
                title = item.get("jobTitle", "") or item.get("roleName", "")
                if email or name:
                    contacts.append({
                        "name": name,
                        "title": title,
                        "email": email.replace("mailto:", ""),
                        "source": "website",
                    })

                for member in item.get("member", []) + item.get("employee", []):
                    if isinstance(member, dict):
                        contacts.append({
                            "name": member.get("name", ""),
                            "title": member.get("jobTitle", ""),
                            "email": member.get("email", "").replace("mailto:", ""),
                            "source": "website",
                        })
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass
    return contacts


def _extract_social_links(soup: BeautifulSoup) -> list[dict]:
    """Extract social media profile URLs that might give us names."""
    contacts = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        for platform, pattern in SOCIAL_PATTERNS.items():
            match = pattern.search(href)
            if match:
                handle = match.group(1)
                # Skip generic company accounts
                if handle.lower() in ("share", "intent", "home", "login", "signup"):
                    continue
                # The link text or nearby text might have a name
                link_text = a.get_text(strip=True)
                parent_text = ""
                if a.parent:
                    parent_text = a.parent.get_text(separator=" ", strip=True)
                # Check if there's a title nearby
                title_match = TITLE_PATTERNS.search(parent_text)
                name_match = NAME_PATTERN.search(parent_text) or NAME_PATTERN.search(link_text)
                if name_match or title_match:
                    contacts.append({
                        "name": name_match.group(0) if name_match else "",
                        "title": title_match.group(0) if title_match else "",
                        "email": "",
                        "source": f"website_{platform}",
                    })
    return contacts


def _extract_contacts_from_html(soup: BeautifulSoup) -> list[dict]:
    contacts = []
    text = soup.get_text(separator=" ", strip=True)

    emails = EMAIL_REGEX.findall(text)
    for a in soup.find_all("a", href=True):
        if a["href"].startswith("mailto:"):
            email = a["href"].replace("mailto:", "").split("?")[0]
            if email and email not in emails:
                emails.append(email)

    # Extract from structured data attributes
    for el in soup.find_all(attrs={"itemprop": "email"}):
        email = el.get("content", "") or el.get("href", "").replace("mailto:", "").split("?")[0] or el.get_text(strip=True)
        if email and EMAIL_REGEX.match(email) and email not in emails:
            emails.append(email)

    for el in soup.find_all(attrs={"data-email": True}):
        email = el["data-email"]
        if email and EMAIL_REGEX.match(email) and email not in emails:
            emails.append(email)

    # Extract names and titles from microdata
    for el in soup.find_all(attrs={"itemprop": "name"}):
        name_text = el.get("content", "") or el.get_text(strip=True)
        name_m = NAME_PATTERN.search(name_text)
        if name_m:
            # Look for a nearby jobTitle
            parent = el.parent
            title_el = parent.find(attrs={"itemprop": "jobTitle"}) if parent else None
            title_text = ""
            if title_el:
                title_text = title_el.get("content", "") or title_el.get_text(strip=True)
            if name_m and (title_text or TITLE_PATTERNS.search(parent.get_text(separator=" ", strip=True) if parent else "")):
                title_final = title_text or (TITLE_PATTERNS.search(parent.get_text(separator=" ", strip=True)).group(0) if parent and TITLE_PATTERNS.search(parent.get_text(separator=" ", strip=True)) else "")
                contacts.append({
                    "name": name_m.group(0),
                    "title": title_final,
                    "email": "",
                    "source": "website_microdata",
                })

    title_matches = TITLE_PATTERNS.findall(text)
    name_matches = NAME_PATTERN.findall(text)

    for email in emails:
        if any(skip in email for skip in ["example.com", "sentry.io", "wixpress", ".png", ".jpg"]):
            continue
        contacts.append({
            "name": "",
            "title": "",
            "email": email,
            "source": "website",
        })

    for name in name_matches:
        nearby_text = ""
        for el in soup.find_all(string=re.compile(re.escape(name))):
            parent = el.parent
            if parent:
                nearby_text = parent.get_text(separator=" ", strip=True)
                break

        title = ""
        for t in title_matches:
            if t in nearby_text:
                title = t
                break

        if title:
            contacts.append({
                "name": name,
                "title": title,
                "email": "",
                "source": "website",
            })

    return contacts


def _find_team_member_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Find links to individual team member pages from a team/about page."""
    links = []
    team_keywords = ["team", "people", "about", "staff", "member", "leadership", "founder", "bio"]
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Make absolute
        if href.startswith("/"):
            href = base_url + href
        elif not href.startswith("http"):
            continue
        # Only follow links on the same domain
        if base_url.split("//")[-1].split("/")[0] not in href:
            continue
        # Check if it looks like a team member page
        href_lower = href.lower()
        link_text = a.get_text(strip=True)
        if any(kw in href_lower for kw in team_keywords):
            # Check for name-like link text
            if NAME_PATTERN.search(link_text) or any(kw in href_lower for kw in ["/team/", "/people/", "/about/", "/staff/", "/member/"]):
                if href not in links:
                    links.append(href)
    return links


_ATS_HOSTS = {
    "boards.greenhouse.io", "boards.eu.greenhouse.io",
    "api.lever.co", "jobs.lever.co",
    "api.ashbyhq.com", "jobs.ashbyhq.com",
    "jobs.workable.com", "apply.workable.com",
    "jobs.smartrecruiters.com", "jobs.jobvite.com",
}


async def scrape_company_contacts(company_url: str) -> list[dict]:
    """
    Scrape company website /about, /team, /contact pages for emails and names.
    Returns list of {"name": str, "title": str, "email": str, "source": "website"}
    """
    if not company_url:
        return []
    if not company_url.startswith(("http://", "https://")):
        company_url = "https://" + company_url

    from urllib.parse import urlparse
    host = urlparse(company_url).netloc.lower().removeprefix("www.")
    if host in _ATS_HOSTS:
        return []

    base_url = company_url.rstrip("/")
    all_contacts = []
    team_member_links = []

    try:
        async with httpx.AsyncClient(
            timeout=10, follow_redirects=True, headers=HEADERS
        ) as client:
            # Phase 1: Scrape standard paths
            for path in CONTACT_PATHS:
                try:
                    resp = await client.get(f"{base_url}{path}")
                    if resp.status_code != 200:
                        continue
                    soup = BeautifulSoup(resp.text, "html.parser")
                    all_contacts.extend(_extract_jsonld_contacts(soup))
                    all_contacts.extend(_extract_contacts_from_html(soup))
                    all_contacts.extend(_extract_social_links(soup))
                    # Collect team member links for phase 2
                    team_member_links.extend(_find_team_member_links(soup, base_url))
                except Exception:
                    continue

            # Phase 2: Follow up to 5 team member links
            followed = 0
            seen_urls = set()
            for link in team_member_links:
                if followed >= 5:
                    break
                if link in seen_urls:
                    continue
                seen_urls.add(link)
                try:
                    resp = await client.get(link)
                    if resp.status_code != 200:
                        continue
                    soup = BeautifulSoup(resp.text, "html.parser")
                    all_contacts.extend(_extract_contacts_from_html(soup))
                    all_contacts.extend(_extract_social_links(soup))
                    followed += 1
                    logger.debug("Followed team member link: %s", link[:80])
                except Exception:
                    continue
    except Exception:
        return []

    seen = set()
    deduped = []
    for c in all_contacts:
        key = (c["email"].lower() if c["email"] else "") or c["name"]
        if key and key not in seen:
            seen.add(key)
            deduped.append(c)

    return deduped
