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


def get_all_html_scrapers() -> list[BaseScraper]:
    return [
        NoDeskScraper(),
        JustRemoteScraper(),
        FourDayWeekScraper(),
        BuiltInScraper(),
        ArcDevScraper(),
    ]
