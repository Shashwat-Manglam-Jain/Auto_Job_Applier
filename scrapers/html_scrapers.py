import asyncio
import logging
import hashlib
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseScraper

logger = logging.getLogger(__name__)


def _text(el) -> str:
    return el.get_text(separator=" ", strip=True) if el else ""


def _strip_html(text: str) -> str:
    if not text:
        return ""
    return BeautifulSoup(text, "lxml").get_text(separator=" ", strip=True)


class NoDeskScraper(BaseScraper):
    name = "nodesk"
    rate_limit = 3.0

    async def _scrape_impl(self) -> list[dict]:
        try:
            resp = await self._get("https://nodesk.co/remote-jobs/engineering/")
            soup = BeautifulSoup(resp.text, "lxml")

            # Job cards are <li> elements with classes like "dt-s dt-ns"
            cards = soup.select("li.dt-s")
            jobs = []
            for card in cards:
                # Title is in h2 > a
                title_el = card.select_one("h2 a[href*='/remote-jobs/']")
                if not title_el:
                    continue
                href = title_el.get("href", "")
                if not href or href == "#":
                    continue
                job_url = urljoin("https://nodesk.co", href)

                title = title_el.get_text(strip=True)
                if not title:
                    continue

                # Company is in h3 > a[href*='/remote-companies/']
                company_el = card.select_one("h3 a[href*='/remote-companies/']")
                company_name = company_el.get_text(strip=True) if company_el else ""

                # Tags from pill spans
                tag_els = card.select("span.br-pill")
                tags = [t.get_text(strip=True) for t in tag_els if t.get_text(strip=True)]

                loc_el = card.select_one("span.fw1")
                location = loc_el.get_text(strip=True) if loc_el else "Remote"

                # Try to find a date element; NoDesk pages don't always show dates
                date_el = card.select_one("time, [class*='date'], [datetime]")
                if date_el:
                    date_str = date_el.get("datetime") or date_el.get_text(strip=True)
                    if not self._is_today(date_str):
                        continue
                else:
                    # No date available on the page -- return all listings
                    logger.debug("[%s] No date element found on card, including job: %s", self.name, title)

                jobs.append(self._job(
                    source_id=hashlib.md5(job_url.encode()).hexdigest(),
                    url=job_url,
                    title=title,
                    company_name=company_name,
                    tags=tags,
                    location=location,
                ))
            return jobs
        except Exception as e:
            logger.error(f"[{self.name}] {e}")
            return []


class JustRemoteScraper(BaseScraper):
    name = "justremote"
    rate_limit = 3.0

    async def _scrape_impl(self) -> list[dict]:
        import json as _json
        import re as _re
        try:
            resp = await self._get("https://justremote.co/remote-developer-jobs")

            # Job data is embedded in window.__PRELOADED_STATE__ JSON
            match = _re.search(
                r"window\.__PRELOADED_STATE__\s*=\s*({.+?})\s*;?\s*</script>",
                resp.text,
                _re.DOTALL,
            )
            if not match:
                logger.warning(f"[{self.name}] __PRELOADED_STATE__ not found")
                return []

            state = _json.loads(match.group(1))
            items = state.get("jobsState", {}).get("entity", {}).get("all", [])

            jobs = []
            for item in items:
                if not item.get("is_active", True):
                    continue
                title = item.get("title", "")
                company = item.get("company_name", "")
                href = item.get("href", "")
                if not title or not href:
                    continue

                # Filter by date if available
                posted_at = item.get("created_at") or item.get("published_at") or item.get("date")
                if posted_at and not self._is_today(posted_at):
                    continue

                job_url = urljoin("https://justremote.co/", href)

                jobs.append(self._job(
                    source_id=str(item.get("id", hashlib.md5(job_url.encode()).hexdigest())),
                    url=job_url,
                    title=title,
                    company_name=company,
                    tags=[item.get("category", "")] if item.get("category") else [],
                    location=item.get("region", "Remote"),
                    posted_at=posted_at or "",
                ))
            if not any(item.get("created_at") or item.get("published_at") or item.get("date") for item in items):
                logger.debug("[%s] No date fields found in job data, returning all active listings", self.name)
            return jobs
        except Exception as e:
            logger.error(f"[{self.name}] {e}")
            return []


class FourDayWeekScraper(BaseScraper):
    name = "4dayweek"
    rate_limit = 3.0

    async def _scrape_impl(self) -> list[dict]:
        try:
            resp = await self._get("https://4dayweek.io/remote-jobs")
            soup = BeautifulSoup(resp.text, "lxml")

            cards = (
                soup.select("[class*='job-card']")
                or soup.select("[class*='job-listing']")
                or soup.select("[class*='listing']")
                or soup.select("article")
            )
            jobs = []
            for card in cards:
                link_el = card.find("a", href=True)
                if not link_el or not link_el.get("href"):
                    continue
                job_url = urljoin("https://4dayweek.io", link_el["href"])

                title_el = card.select_one("h2, h3, [class*='title']")
                company_el = card.select_one("[class*='company']")

                salary_text = _text(card.select_one("[class*='salary']"))
                salary_min, salary_max = self._parse_salary(salary_text)

                loc_el = card.select_one("[class*='location']")
                location = _text(loc_el) if loc_el else "Remote"

                # Try to find a date element for filtering
                date_el = card.select_one("time, [class*='date'], [datetime]")
                if date_el:
                    date_str = date_el.get("datetime") or date_el.get_text(strip=True)
                    if not self._is_today(date_str):
                        continue
                else:
                    logger.debug("[%s] No date element found on card, including job: %s", self.name, _text(title_el))

                jobs.append(self._job(
                    source_id=hashlib.md5(job_url.encode()).hexdigest(),
                    url=job_url,
                    title=_text(title_el) or _text(link_el),
                    company_name=_text(company_el),
                    salary_min=salary_min,
                    salary_max=salary_max,
                    location=location,
                ))
            return jobs
        except Exception as e:
            logger.error(f"[{self.name}] {e}")
            return []

    @staticmethod
    def _parse_salary(text: str) -> tuple[int | None, int | None]:
        if not text:
            return None, None
        import re
        numbers = re.findall(r"[\d,]+", text.replace(",", ""))
        nums = [int(n) for n in numbers if n.isdigit() and int(n) > 1000]
        if len(nums) >= 2:
            return min(nums), max(nums)
        if len(nums) == 1:
            return nums[0], nums[0]
        return None, None


class BuiltInScraper(BaseScraper):
    """Scrape BuiltIn for remote engineering/dev jobs."""
    name = "builtin"
    rate_limit = 3.0

    async def _scrape_impl(self) -> list[dict]:
        try:
            resp = await self._get("https://builtin.com/jobs/remote/dev-engineering")
            soup = BeautifulSoup(resp.text, "lxml")

            # BuiltIn uses data-id="job-card" on each card container
            cards = soup.select('div[data-id="job-card"]')
            jobs = []
            for card in cards:
                # Title link uses data-id="job-card-title"
                title_el = card.select_one('a[data-id="job-card-title"]')
                if not title_el:
                    continue
                href = title_el.get("data-alias") or title_el.get("href", "")
                if not href or href == "#":
                    continue
                job_url = urljoin("https://builtin.com", href)

                title = title_el.get_text(strip=True)
                if not title:
                    continue

                # Company name uses data-id="company-title"
                company_el = card.select_one('a[data-id="company-title"] span')
                company_name = company_el.get_text(strip=True) if company_el else ""

                # Extract the numeric job ID from the card's id attribute
                card_id = card.get("id", "")  # e.g. "job-card-9883398"
                source_id = card_id.replace("job-card-", "") if card_id.startswith("job-card-") else hashlib.md5(job_url.encode()).hexdigest()

                loc_el = card.select_one('[data-id="job-card-location"]')
                location = loc_el.get_text(strip=True) if loc_el else "Remote"

                # Try to find a date element for filtering
                date_el = card.select_one('time, [data-id*="date"], [class*="date"]')
                if date_el:
                    date_str = date_el.get("datetime") or date_el.get_text(strip=True)
                    if not self._is_today(date_str):
                        continue
                else:
                    logger.debug("[%s] No date element found on card, including job: %s", self.name, title)

                jobs.append(self._job(
                    source_id=source_id,
                    url=job_url,
                    title=title,
                    company_name=company_name,
                    location=location,
                ))
            return jobs
        except Exception as e:
            logger.error(f"[{self.name}] {e}")
            return []


# ---------------------------------------------------------------------------
# NEW SCRAPERS
# ---------------------------------------------------------------------------


class ArcDevScraper(BaseScraper):
    """Scrape Arc.dev for remote developer jobs."""
    name = "arcdev"
    rate_limit = 3.0

    async def _scrape_impl(self) -> list[dict]:
        try:
            resp = await self._get("https://arc.dev/remote-jobs/developer")
            soup = BeautifulSoup(resp.text, "lxml")

            # Arc.dev lists jobs in card containers
            cards = (
                soup.select("div[class*='job-card'], div[class*='JobCard']")
                or soup.select("[class*='job-listing'], [class*='listing']")
                or soup.select("a[href*='/remote-jobs/']")
            )

            jobs = []
            seen = set()

            if not cards:
                # Fallback: look for job links
                cards = soup.select("a[href*='/remote-jobs/']")

            for card in cards:
                if card.name == "a":
                    link_el = card
                else:
                    link_el = card.find("a", href=True)
                if not link_el or not link_el.get("href"):
                    continue

                href = link_el["href"]
                if not href.startswith("http"):
                    href = urljoin("https://arc.dev", href)

                # Skip navigation/category links
                if href.rstrip("/") == "https://arc.dev/remote-jobs/developer":
                    continue
                if href in seen:
                    continue
                seen.add(href)

                title_el = card.select_one("h2, h3, h4, [class*='title']") if card.name != "a" else None
                title = _text(title_el) if title_el else link_el.get_text(strip=True)
                if not title or len(title) < 3:
                    continue

                company_el = card.select_one("[class*='company']") if card.name != "a" else None
                company_name = _text(company_el) if company_el else ""

                loc_el = card.select_one("[class*='location']") if card.name != "a" else None
                location = _text(loc_el) if loc_el else "Remote"

                # Try to find tags/skills
                tag_els = card.select("[class*='tag'], [class*='skill'], [class*='badge']") if card.name != "a" else []
                tags = [_text(t) for t in tag_els if _text(t)]

                jobs.append(self._job(
                    source_id=hashlib.md5(href.encode()).hexdigest(),
                    url=href,
                    title=title,
                    company_name=company_name,
                    tags=tags or ["remote", "developer"],
                    location=location,
                ))
            return jobs
        except Exception as e:
            logger.error(f"[{self.name}] {e}")
            return []


class LinkedInGuestScraper(BaseScraper):
    name = "linkedin"
    rate_limit = 3.0

    SEARCHES = [
        "remote+software+engineer",
        "remote+developer",
        "remote+backend+engineer",
        "remote+frontend+developer",
        "remote+fullstack+engineer",
        "remote+devops+engineer",
        "remote+python+developer",
    ]

    async def _scrape_impl(self) -> list[dict]:
        try:
            jobs = []
            seen = set()
            for query in self.SEARCHES:
                for start in (0, 25):
                    try:
                        resp = await self._get(
                            f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
                            f"?keywords={query}&f_WT=2&f_TPR=r86400&start={start}"
                        )
                    except Exception:
                        continue
                    soup = BeautifulSoup(resp.text, "lxml")
                    cards = soup.select("li div.base-card")
                    for card in cards:
                        title_el = card.select_one("h3.base-search-card__title")
                        company_el = card.select_one("h4.base-search-card__subtitle")
                        link_el = card.select_one("a.base-card__full-link") or card.find("a", href=True)
                        date_el = card.select_one("time")
                        loc_el = card.select_one(".job-search-card__location")

                        if not title_el or not link_el:
                            continue

                        href = link_el.get("href", "")
                        if not href:
                            continue
                        clean_url = href.split("?")[0]
                        if clean_url in seen:
                            continue
                        seen.add(clean_url)

                        date_str = date_el.get("datetime") if date_el else None
                        if date_str and not self._is_today(date_str):
                            continue

                        jobs.append(self._job(
                            source_id=hashlib.md5(clean_url.encode()).hexdigest(),
                            url=clean_url,
                            title=_text(title_el),
                            company_name=_text(company_el),
                            location=_text(loc_el) or "Remote",
                            posted_at=date_str or "",
                        ))
            return jobs
        except Exception as e:
            logger.error(f"[{self.name}] {e}")
            return []


class DailyRemoteScraper(BaseScraper):
    name = "dailyremote"
    rate_limit = 3.0

    async def _scrape_impl(self) -> list[dict]:
        try:
            resp = await self._get("https://dailyremote.com/remote-software-development-jobs")
            soup = BeautifulSoup(resp.text, "lxml")

            jobs = []
            seen = set()
            for link in soup.select('a[href*="/remote-job/"]'):
                title = link.get_text(strip=True)
                if not title or title.upper() == "APPLY":
                    continue
                href = link.get("href", "")
                if not href or href in seen:
                    continue
                seen.add(href)

                job_url = urljoin("https://dailyremote.com", href)

                parent = link.parent
                gparent = parent.parent if parent else None
                company_name = ""
                posted_ago = ""
                if gparent:
                    company_div = gparent.select_one(".company-name")
                    if company_div:
                        spans = company_div.find_all("span")
                        if spans:
                            company_name = spans[0].get_text(strip=True)
                        for span in spans:
                            text = span.get_text(strip=True)
                            if "ago" in text.lower():
                                posted_ago = text

                if posted_ago:
                    ago_lower = posted_ago.lower()
                    if any(x in ago_lower for x in ["week", "month", "year"]):
                        continue

                jobs.append(self._job(
                    source_id=hashlib.md5(job_url.encode()).hexdigest(),
                    url=job_url,
                    title=title,
                    company_name=company_name,
                    location="Remote",
                    posted_at=posted_ago,
                ))
            return jobs
        except Exception as e:
            logger.error(f"[{self.name}] {e}")
            return []


class EURemoteJobsScraper(BaseScraper):
    name = "euremotejobs"
    rate_limit = 3.0

    async def _scrape_impl(self) -> list[dict]:
        try:
            resp = await self._get("https://euremotejobs.com/")
            soup = BeautifulSoup(resp.text, "lxml")

            jobs = []
            cards = soup.select("a.job-card-link")
            for card in cards:
                job_url = card.get("href", "")
                if not job_url or "/job/" not in job_url:
                    continue

                title_el = card.select_one("h2.job-title")
                title = _text(title_el) if title_el else ""
                if not title:
                    continue

                logo_img = card.select_one("img.company_logo")
                company_name = logo_img.get("alt", "") if logo_img else ""

                posted_el = card.select_one("[class*='date'], [class*='posted'], time")
                posted_text = _text(posted_el) if posted_el else card.get_text(" ", strip=True)
                posted_lower = posted_text.lower()
                if any(x in posted_lower for x in ["months ago", "year ago", "years ago"]):
                    continue

                loc_el = card.select_one("[class*='location']")
                location = _text(loc_el) if loc_el else "Europe Remote"

                jobs.append(self._job(
                    source_id=hashlib.md5(job_url.encode()).hexdigest(),
                    url=job_url,
                    title=title,
                    company_name=company_name,
                    location=location or "Europe Remote",
                    tags=["remote", "europe"],
                ))
            return jobs
        except Exception as e:
            logger.error(f"[{self.name}] {e}")
            return []


class YCJobsScraper(BaseScraper):
    name = "ycjobs"
    rate_limit = 3.0

    async def _scrape_impl(self) -> list[dict]:
        try:
            resp = await self._get("https://www.ycombinator.com/jobs")
            soup = BeautifulSoup(resp.text, "lxml")

            jobs = []
            seen = set()
            for link in soup.select('a[href*="/companies/"][href*="/jobs/"]'):
                href = link.get("href", "")
                if href in seen:
                    continue
                seen.add(href)

                job_url = urljoin("https://www.ycombinator.com", href)
                title = link.get_text(strip=True)
                if not title or len(title) < 3:
                    continue

                company_name = ""
                parts = href.split("/companies/")
                if len(parts) > 1:
                    slug = parts[1].split("/")[0]
                    company_name = slug.replace("-", " ").title()

                jobs.append(self._job(
                    source_id=hashlib.md5(job_url.encode()).hexdigest(),
                    url=job_url,
                    title=title,
                    company_name=company_name,
                    tags=["yc", "startup"],
                    location="Remote",
                ))
            return jobs
        except Exception as e:
            logger.error(f"[{self.name}] {e}")
            return []


class IndeedScraper(BaseScraper):
    """Scrape Indeed for remote software engineering jobs."""
    name = "indeed"
    rate_limit = 3.0

    SEARCHES = [
        "remote software engineer",
        "remote developer",
        "remote backend engineer",
        "remote frontend developer",
        "remote devops engineer",
    ]

    async def _scrape_impl(self) -> list[dict]:
        try:
            jobs = []
            seen = set()
            for query in self.SEARCHES:
                for start in (0, 10):
                    try:
                        resp = await self._get(
                            f"https://www.indeed.com/jobs"
                            f"?q={query.replace(' ', '+')}"
                            f"&l=Remote&sc=0kf%3Aattr%28DSQF7%29%3B"
                            f"&fromage=1&start={start}"
                        )
                    except Exception:
                        continue

                    soup = BeautifulSoup(resp.text, "lxml")
                    cards = soup.select(".job_seen_beacon, .resultContent")

                    for card in cards:
                        # Extract title
                        title_el = card.select_one("h2.jobTitle a") or card.select_one("a[data-jk]")
                        if not title_el:
                            continue

                        title = title_el.get_text(strip=True)
                        if not title:
                            continue

                        # Extract job ID and build URL
                        jk = title_el.get("data-jk", "")
                        if not jk:
                            # Try to find data-jk on a parent or sibling
                            jk_el = card.select_one("[data-jk]")
                            jk = jk_el.get("data-jk", "") if jk_el else ""
                        if not jk:
                            # Fall back to href parsing
                            href = title_el.get("href", "")
                            if "jk=" in href:
                                jk = href.split("jk=")[-1].split("&")[0]
                        if not jk:
                            continue

                        if jk in seen:
                            continue
                        seen.add(jk)

                        job_url = f"https://www.indeed.com/viewjob?jk={jk}"

                        # Extract company name
                        company_el = (
                            card.select_one('span[data-testid="company-name"]')
                            or card.select_one(".companyName")
                        )
                        company_name = company_el.get_text(strip=True) if company_el else ""

                        # Extract location
                        loc_el = (
                            card.select_one('div[data-testid="text-location"]')
                            or card.select_one(".companyLocation")
                        )
                        location = loc_el.get_text(strip=True) if loc_el else ""

                        # Filter for remote jobs only
                        loc_lower = location.lower()
                        if location and "remote" not in loc_lower:
                            continue

                        # Extract date
                        date_el = card.select_one(".date")
                        date_str = date_el.get_text(strip=True) if date_el else ""
                        if date_str:
                            date_lower = date_str.lower()
                            # Skip listings older than a couple of days
                            if any(x in date_lower for x in ["week", "month", "30+"]):
                                continue

                        jobs.append(self._job(
                            source_id=jk,
                            url=job_url,
                            title=title,
                            company_name=company_name,
                            location=location or "Remote",
                            posted_at=date_str,
                        ))
            return jobs
        except Exception as e:
            logger.error(f"[{self.name}] {e}")
            return []


class WellfoundScraper(BaseScraper):
    """Scrape Wellfound (AngelList) for remote engineering jobs using Playwright."""
    name = "wellfound"
    rate_limit = 3.0

    async def _scrape_impl(self) -> list[dict]:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("[%s] playwright not installed, skipping", self.name)
            return []

        jobs = []
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(
                    "https://wellfound.com/jobs?remote=true&role=engineering",
                    wait_until="networkidle",
                    timeout=30000,
                )

                # Scroll down 3 times to trigger lazy-loaded job cards
                for _ in range(3):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(2)

                html = await page.content()
                await browser.close()

            soup = BeautifulSoup(html, "lxml")

            # Wellfound is a React app; try multiple card selectors
            cards = (
                soup.select("[class*='job-card'], [class*='JobCard']")
                or soup.select("[class*='styles_jobListing'], [class*='JobListing']")
                or soup.select("div[class*='listing'] a[href*='/jobs/']")
                or soup.select("a[href*='/jobs/']")
            )

            seen = set()
            for card in cards:
                # Find the link element
                if card.name == "a" and card.get("href"):
                    link_el = card
                else:
                    link_el = card.find("a", href=True)
                if not link_el:
                    continue

                href = link_el.get("href", "")
                if not href or href == "#":
                    continue
                if not href.startswith("http"):
                    href = urljoin("https://wellfound.com", href)

                # Skip non-job links (navigation, filters, etc.)
                if "/jobs" not in href or href.rstrip("/") == "https://wellfound.com/jobs":
                    continue
                if href in seen:
                    continue
                seen.add(href)

                # Extract title
                title_el = card.select_one(
                    "h2, h3, h4, "
                    "[class*='title'], [class*='Title'], "
                    "[class*='jobTitle'], [class*='JobTitle']"
                ) if card.name != "a" else None
                title = _text(title_el) if title_el else link_el.get_text(strip=True)
                if not title or len(title) < 3:
                    continue

                # Extract company name
                company_el = card.select_one(
                    "[class*='company'], [class*='Company'], "
                    "[class*='companyName'], [class*='CompanyName']"
                ) if card.name != "a" else None
                company_name = _text(company_el) if company_el else ""

                # Extract location
                loc_el = card.select_one(
                    "[class*='location'], [class*='Location']"
                ) if card.name != "a" else None
                location = _text(loc_el) if loc_el else "Remote"

                # Extract tags (skills, technologies)
                tag_els = card.select(
                    "[class*='tag'], [class*='Tag'], "
                    "[class*='skill'], [class*='Skill'], "
                    "[class*='badge'], [class*='Badge']"
                ) if card.name != "a" else []
                tags = [_text(t) for t in tag_els if _text(t)]

                jobs.append(self._job(
                    source_id=hashlib.md5(href.encode()).hexdigest(),
                    url=href,
                    title=title,
                    company_name=company_name,
                    tags=tags or ["remote", "engineering"],
                    location=location,
                ))
            return jobs
        except Exception as e:
            logger.error(f"[{self.name}] {e}")
            return []


class WeWorkRemotelyScraper(BaseScraper):
    """Scrape We Work Remotely RSS feed for remote programming jobs."""
    name = "weworkremotely"
    rate_limit = 3.0

    RSS_URLS = [
        "https://weworkremotely.com/categories/remote-programming-jobs",
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs",
    ]

    async def _scrape_impl(self) -> list[dict]:
        try:
            jobs = []
            seen = set()

            for rss_url in self.RSS_URLS:
                try:
                    resp = await self._get(rss_url)
                except Exception:
                    continue

                soup = BeautifulSoup(resp.text, "xml")
                items = soup.find_all("item")

                for item in items:
                    title_el = item.find("title")
                    link_el = item.find("link")
                    if not title_el or not link_el:
                        continue

                    raw_title = title_el.get_text(strip=True)
                    link = link_el.get_text(strip=True)
                    if not raw_title or not link:
                        continue
                    if link in seen:
                        continue
                    seen.add(link)

                    # Title format: "Company: Job Title"
                    company_name = ""
                    title = raw_title
                    if ": " in raw_title:
                        company_name, title = raw_title.split(": ", 1)

                    pub_date = ""
                    pub_el = item.find("pubDate")
                    if pub_el:
                        pub_date = pub_el.get_text(strip=True)

                    jobs.append(self._job(
                        source_id=hashlib.md5(link.encode()).hexdigest(),
                        url=link,
                        title=title,
                        company_name=company_name,
                        posted_at=pub_date,
                        location="Remote",
                    ))
            return jobs
        except Exception as e:
            logger.error(f"[{self.name}] {e}")
            return []


class ToptalScraper(BaseScraper):
    """Scrape Toptal for freelance jobs."""
    name = "toptal"
    rate_limit = 3.0

    async def _scrape_impl(self) -> list[dict]:
        try:
            resp = await self._get("https://www.toptal.com/freelance-jobs")
            soup = BeautifulSoup(resp.text, "lxml")

            jobs = []
            seen = set()

            # Look for links containing /freelance-jobs/ (individual job pages)
            links = soup.select("a[href*='/freelance-jobs/']")
            for link_el in links:
                href = link_el.get("href", "")
                if not href:
                    continue
                if not href.startswith("http"):
                    href = urljoin("https://www.toptal.com", href)

                # Skip the listing page itself
                if href.rstrip("/") == "https://www.toptal.com/freelance-jobs":
                    continue
                if href in seen:
                    continue
                seen.add(href)

                title = link_el.get_text(strip=True)
                if not title or len(title) < 3:
                    continue

                # Try to find company from parent card
                parent = link_el.parent
                company_name = ""
                if parent:
                    company_el = parent.select_one("[class*='company'], [class*='Company']")
                    company_name = _text(company_el) if company_el else ""

                jobs.append(self._job(
                    source_id=hashlib.md5(href.encode()).hexdigest(),
                    url=href,
                    title=title,
                    company_name=company_name,
                    location="Remote",
                ))
            return jobs
        except Exception as e:
            logger.error(f"[{self.name}] {e}")
            return []


def get_all_html_scrapers() -> list[BaseScraper]:
    return [
        NoDeskScraper(),
        JustRemoteScraper(),
        FourDayWeekScraper(),
        BuiltInScraper(),
        ArcDevScraper(),
        LinkedInGuestScraper(),
        DailyRemoteScraper(),
        EURemoteJobsScraper(),
        YCJobsScraper(),
        IndeedScraper(),
        WellfoundScraper(),
        WeWorkRemotelyScraper(),
    ]
