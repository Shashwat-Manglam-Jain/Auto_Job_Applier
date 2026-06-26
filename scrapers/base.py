import httpx
import asyncio
import logging
import random
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]


class BaseScraper(ABC):
    name: str = "base"
    rate_limit: float = 1.0

    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": random.choice(USER_AGENTS)},
            follow_redirects=True,
        )

    @abstractmethod
    async def _scrape_impl(self) -> list[dict]:
        pass

    async def scrape(self) -> list[dict]:
        try:
            logger.info("[%s] Starting scrape...", self.name)
            jobs = await self._scrape_impl()
            logger.info("[%s] Completed: %d jobs found", self.name, len(jobs))
            return jobs
        except Exception as e:
            logger.error("[%s] Scrape failed: %s", self.name, e, exc_info=True)
            return []

    async def _get(self, url, **kwargs) -> httpx.Response:
        for attempt in range(3):
            try:
                resp = await self.client.get(url, **kwargs)
                resp.raise_for_status()
                await asyncio.sleep(self.rate_limit)
                return resp
            except Exception as e:
                if attempt == 2:
                    logger.warning(f"[{self.name}] failed after 3 attempts: {url} — {e}")
                    raise
                backoff = self.rate_limit * (2 ** attempt)
                await asyncio.sleep(backoff)

    async def _get_json(self, url, **kwargs) -> dict:
        resp = await self._get(url, **kwargs)
        return resp.json()

    def _is_today(self, date_str_or_dt) -> bool:
        if date_str_or_dt is None:
            return True

        now = datetime.now(timezone.utc)
        dt = None

        if isinstance(date_str_or_dt, datetime):
            dt = date_str_or_dt if date_str_or_dt.tzinfo else date_str_or_dt.replace(tzinfo=timezone.utc)
        elif isinstance(date_str_or_dt, (int, float)):
            # Unix timestamp
            dt = datetime.fromtimestamp(date_str_or_dt, tz=timezone.utc)
        elif isinstance(date_str_or_dt, str):
            # Try parsing as unix timestamp string first
            try:
                ts = float(date_str_or_dt)
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                return (now - dt) < timedelta(hours=36)
            except (ValueError, OSError):
                pass

            for fmt in [
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%d",
                "%a, %d %b %Y %H:%M:%S %z",
                "%a, %d %b %Y %H:%M:%S %Z",
                "%B %d, %Y",
                "%d %b %Y",
            ]:
                try:
                    dt = datetime.strptime(date_str_or_dt, fmt)
                    if not dt.tzinfo:
                        dt = dt.replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
            else:
                return True
        else:
            return True

        # 36h window to catch timezone edge cases
        return (now - dt) < timedelta(hours=36)

    def _job(
        self,
        source_id: str,
        url: str,
        title: str,
        company_name: str,
        company_url: str = "",
        description: str = "",
        tags: list[str] | None = None,
        salary_min: int | None = None,
        salary_max: int | None = None,
        posted_at: str = "",
        location: str = "",
    ) -> dict:
        return {
            "source": self.name,
            "source_id": str(source_id),
            "url": url,
            "title": title,
            "company_name": company_name,
            "company_url": company_url,
            "description": description,
            "tags": tags or [],
            "salary_min": salary_min,
            "salary_max": salary_max,
            "posted_at": posted_at,
            "location": location,
        }

    async def close(self):
        await self.client.aclose()
