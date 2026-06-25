import re
import logging
import hashlib
from datetime import datetime, timezone
from time import mktime

import feedparser

from .base import BaseScraper

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    if not text:
        return ""
    return _HTML_TAG_RE.sub("", text).strip()


def _struct_time_to_dt(st) -> datetime | None:
    if not st:
        return None
    try:
        return datetime.fromtimestamp(mktime(st), tz=timezone.utc)
    except Exception:
        return None


class WeWorkRemotelyScraper(BaseScraper):
    name = "weworkremotely"
    rate_limit = 2.0

    FEEDS = [
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
        "https://weworkremotely.com/categories/remote-design-jobs.rss",
    ]

    async def _scrape_impl(self) -> list[dict]:
        try:
            jobs = []
            seen = set()
            for feed_url in self.FEEDS:
                resp = await self._get(feed_url)
                feed = feedparser.parse(resp.text)
                for entry in feed.entries:
                    url = getattr(entry, "link", "")
                    if url in seen:
                        continue
                    seen.add(url)

                    pub_dt = _struct_time_to_dt(getattr(entry, "published_parsed", None))
                    if not self._is_today(pub_dt):
                        continue

                    raw_title = getattr(entry, "title", "")
                    if ":" in raw_title:
                        company, title = raw_title.split(":", 1)
                        company = company.strip()
                        title = title.strip()
                    else:
                        company, title = "", raw_title

                    jobs.append(self._job(
                        source_id=hashlib.md5(url.encode()).hexdigest(),
                        url=url,
                        title=title,
                        company_name=company,
                        description=_strip_html(getattr(entry, "summary", "")),
                        posted_at=pub_dt.isoformat() if pub_dt else None,
                        location="Remote",
                    ))
            return jobs
        except Exception as e:
            logger.error(f"[{self.name}] {e}")
            return []


class WorkingNomadsScraper(BaseScraper):
    name = "workingnomads"
    rate_limit = 2.0

    TECH_KEYWORDS = {
        "development", "programming", "engineering", "devops", "data",
        "sysadmin", "design", "software", "developer", "backend", "frontend",
        "fullstack", "full-stack", "cloud", "infrastructure", "security",
        "machine learning", "ai", "ml",
    }

    async def _scrape_impl(self) -> list[dict]:
        # RSS feed is dead, but JSON API works
        try:
            resp = await self._get("https://www.workingnomads.com/api/exposed_jobs/")
            data = resp.json()
            if not isinstance(data, list):
                return []
            jobs = []
            for item in data:
                if not self._is_today(item.get("pub_date")):
                    continue
                # Filter to tech categories
                cat = (item.get("category_name") or "").lower()
                title_lower = (item.get("title") or "").lower()
                tags_str = (item.get("tags") or "").lower()
                combined = f"{cat} {title_lower} {tags_str}"
                if not any(kw in combined for kw in self.TECH_KEYWORDS):
                    continue
                url = item.get("url", "")
                tags = [t.strip() for t in (item.get("tags") or "").split(",") if t.strip()]
                desc = item.get("description", "")
                if desc:
                    desc = _strip_html(desc)
                jobs.append(self._job(
                    source_id=hashlib.md5(url.encode()).hexdigest(),
                    url=url,
                    title=item.get("title", ""),
                    company_name=item.get("company_name", ""),
                    description=desc,
                    tags=tags,
                    posted_at=item.get("pub_date", ""),
                    location=item.get("location", "Remote"),
                ))
            return jobs
        except Exception as e:
            logger.error(f"[{self.name}] {e}")
            return []


class GolangJobsScraper(BaseScraper):
    name = "golangjobs"
    rate_limit = 2.0

    async def _scrape_impl(self) -> list[dict]:
        try:
            resp = await self._get("https://www.golangprojects.com/rss.xml")
            feed = feedparser.parse(resp.text)
            jobs = []
            for entry in feed.entries:
                title = getattr(entry, "title", "")
                description = getattr(entry, "summary", "")
                combined = (title + " " + description).lower()
                if "remote" not in combined:
                    continue

                pub_dt = _struct_time_to_dt(getattr(entry, "published_parsed", None))
                # Skip entries with no parseable date instead of accepting all
                if pub_dt is None:
                    continue
                if not self._is_today(pub_dt):
                    continue

                url = getattr(entry, "link", "")
                # Company name is often after "@" in the title
                company_name = getattr(entry, "author", "")
                if not company_name and " @ " in title:
                    company_name = title.split(" @ ", 1)[1].strip()
                elif not company_name and "| " in title and " @ " not in title:
                    parts = title.split("|")
                    if len(parts) >= 2:
                        company_name = parts[-1].strip()
                jobs.append(self._job(
                    source_id=hashlib.md5(url.encode()).hexdigest(),
                    url=url,
                    title=title,
                    company_name=company_name,
                    description=_strip_html(description),
                    tags=["golang", "go"],
                    posted_at=pub_dt.isoformat() if pub_dt else "",
                    location="Remote",
                ))
            return jobs
        except Exception as e:
            logger.error(f"[{self.name}] {e}")
            return []


class DribbbleJobsScraper(BaseScraper):
    """Scrape Dribbble Jobs RSS feed for design/dev jobs."""
    name = "dribbble"
    rate_limit = 2.0

    async def _scrape_impl(self) -> list[dict]:
        try:
            resp = await self._get("https://dribbble.com/jobs.rss")
            feed = feedparser.parse(resp.text)
            jobs = []
            for entry in feed.entries:
                pub_dt = _struct_time_to_dt(getattr(entry, "published_parsed", None))
                if pub_dt is not None and not self._is_today(pub_dt):
                    continue

                url = getattr(entry, "link", "")
                title = getattr(entry, "title", "")
                if not url or not title:
                    continue

                # Dribbble titles are often "Job Title at Company"
                company_name = ""
                if " at " in title:
                    company_name = title.rsplit(" at ", 1)[1].strip()

                description = _strip_html(getattr(entry, "summary", ""))

                # Extract location from description or default to Remote
                location = "Remote"

                jobs.append(self._job(
                    source_id=hashlib.md5(url.encode()).hexdigest(),
                    url=url,
                    title=title,
                    company_name=company_name,
                    description=description,
                    tags=["design"],
                    posted_at=pub_dt.isoformat() if pub_dt else "",
                    location=location,
                ))
            return jobs
        except Exception as e:
            logger.error(f"[{self.name}] {e}")
            return []


class LaravelJobsScraper(BaseScraper):
    """Scrape LaraJobs RSS feed for Laravel/PHP jobs."""
    name = "larajobs"
    rate_limit = 2.0

    async def _scrape_impl(self) -> list[dict]:
        try:
            resp = await self._get("https://larajobs.com/feed")
            feed = feedparser.parse(resp.text)
            jobs = []
            for entry in feed.entries:
                pub_dt = _struct_time_to_dt(getattr(entry, "published_parsed", None))
                if pub_dt is not None and not self._is_today(pub_dt):
                    continue

                url = getattr(entry, "link", "")
                title = getattr(entry, "title", "")
                if not url or not title:
                    continue

                # Try to extract company from title or author
                company_name = getattr(entry, "author", "")
                if not company_name and " at " in title:
                    company_name = title.rsplit(" at ", 1)[1].strip()

                description = _strip_html(getattr(entry, "summary", ""))

                # Extract tags from categories if available
                tags = ["laravel", "php"]
                for tag in getattr(entry, "tags", []):
                    term = getattr(tag, "term", "")
                    if term and term.lower() not in ("laravel", "php"):
                        tags.append(term)

                jobs.append(self._job(
                    source_id=hashlib.md5(url.encode()).hexdigest(),
                    url=url,
                    title=title,
                    company_name=company_name,
                    description=description,
                    tags=tags,
                    posted_at=pub_dt.isoformat() if pub_dt else "",
                    location="Remote",
                ))
            return jobs
        except Exception as e:
            logger.error(f"[{self.name}] {e}")
            return []


class VueJobsScraper(BaseScraper):
    name = "vuejobs"
    rate_limit = 2.0

    async def _scrape_impl(self) -> list[dict]:
        try:
            resp = await self._get("https://vuejobs.com/feed")
            feed = feedparser.parse(resp.text)
            jobs = []
            for entry in feed.entries:
                pub_dt = _struct_time_to_dt(getattr(entry, "published_parsed", None))
                if pub_dt is not None and not self._is_today(pub_dt):
                    continue

                url = getattr(entry, "link", "")
                title = getattr(entry, "title", "")
                if not url or not title:
                    continue

                if "?utm_source=" in url:
                    url = url.split("?utm_source=")[0]

                company_name = ""
                slug = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
                if slug:
                    parts = slug.rsplit("-", 1)
                    if len(parts) >= 2:
                        company_name = parts[0].replace("-", " ").title()

                description = _strip_html(getattr(entry, "summary", ""))

                jobs.append(self._job(
                    source_id=hashlib.md5(url.encode()).hexdigest(),
                    url=url,
                    title=title,
                    company_name=company_name,
                    description=description[:2000],
                    tags=["vue", "javascript", "frontend"],
                    posted_at=pub_dt.isoformat() if pub_dt else "",
                    location="Remote",
                ))
            return jobs
        except Exception as e:
            logger.error(f"[{self.name}] {e}")
            return []


def get_all_rss_scrapers() -> list[BaseScraper]:
    return [
        WeWorkRemotelyScraper(),
        WorkingNomadsScraper(),
        GolangJobsScraper(),
        DribbbleJobsScraper(),
        LaravelJobsScraper(),
        VueJobsScraper(),
    ]
