import re
import asyncio
import logging
from datetime import datetime, timezone

from .base import BaseScraper

logger = logging.getLogger(__name__)


class RemoteOKScraper(BaseScraper):
    name = "remoteok"

    async def _scrape_impl(self) -> list[dict]:
        # Single endpoint, returns all recent jobs — no pagination needed
        data = await self._get_json("https://remoteok.com/api")
        jobs = []
        for item in data[1:]:
            if not self._is_today(item.get("date")):
                continue
            salary_min = None
            salary_max = None
            try:
                salary_min = int(item["salary_min"]) if item.get("salary_min") else None
                salary_max = int(item["salary_max"]) if item.get("salary_max") else None
            except (ValueError, TypeError):
                pass
            jobs.append(self._job(
                source_id=item.get("id", ""),
                url=item.get("url", f"https://remoteok.com/remote-jobs/{item.get('id', '')}"),
                title=item.get("position", ""),
                company_name=item.get("company", ""),
                description=item.get("description", ""),
                tags=item.get("tags", []),
                salary_min=salary_min,
                salary_max=salary_max,
                posted_at=item.get("date", ""),
                location=item.get("location", "Remote"),
            ))
        return jobs


class RemotiveScraper(BaseScraper):
    name = "remotive"

    async def _scrape_impl(self) -> list[dict]:
        jobs = []
        for page in range(1, 6):
            try:
                data = await self._get_json(
                    f"https://remotive.com/api/remote-jobs?category=software-dev&limit=100&page={page}"
                )
            except Exception:
                logger.debug(f"[{self.name}] Page {page} failed, stopping pagination")
                break
            page_jobs = data.get("jobs", [])
            if not page_jobs:
                break
            for item in page_jobs:
                if not self._is_today(item.get("publication_date")):
                    continue
                salary_min = None
                salary_max = None
                if item.get("salary"):
                    sal = str(item["salary"])
                    nums = re.findall(r"[\d,]+", sal)
                    nums = [int(n.replace(",", "")) for n in nums]
                    if len(nums) >= 2:
                        salary_min, salary_max = nums[0], nums[1]
                    elif len(nums) == 1:
                        salary_min = nums[0]
                jobs.append(self._job(
                    source_id=item.get("id", ""),
                    url=item.get("url", ""),
                    title=item.get("title", ""),
                    company_name=item.get("company_name", ""),
                    description=item.get("description", ""),
                    tags=item.get("tags", []),
                    salary_min=salary_min,
                    salary_max=salary_max,
                    posted_at=item.get("publication_date", ""),
                    location=item.get("candidate_required_location", "Remote"),
                ))
        return jobs


class ArbeitnowScraper(BaseScraper):
    name = "arbeitnow"

    async def _scrape_impl(self) -> list[dict]:
        jobs = []
        for page in range(1, 4):
            try:
                data = await self._get_json(
                    f"https://www.arbeitnow.com/api/job-board-api?page={page}"
                )
            except Exception:
                logger.debug(f"[{self.name}] Page {page} failed, stopping pagination")
                break
            page_items = data.get("data", [])
            if not page_items:
                break
            for item in page_items:
                if not item.get("remote", False):
                    continue
                if not self._is_today(item.get("created_at")):
                    continue
                jobs.append(self._job(
                    source_id=item.get("slug", ""),
                    url=item.get("url", ""),
                    title=item.get("title", ""),
                    company_name=item.get("company_name", ""),
                    description=item.get("description", ""),
                    tags=item.get("tags", []),
                    posted_at=item.get("created_at", ""),
                    location=item.get("location", "Remote"),
                ))
        return jobs


class JobicyScraper(BaseScraper):
    name = "jobicy"

    async def _scrape_impl(self) -> list[dict]:
        jobs = []
        try:
            data = await self._get_json(
                "https://jobicy.com/api/v2/remote-jobs?count=50"
            )
        except Exception:
            logger.debug(f"[{self.name}] API request failed")
            return []
        page_jobs = data.get("jobs", [])
        for item in page_jobs:
            if not self._is_today(item.get("pubDate")):
                continue
            jobs.append(self._job(
                source_id=item.get("id", ""),
                url=item.get("url", ""),
                title=item.get("jobTitle", ""),
                company_name=item.get("companyName", ""),
                description=item.get("jobDescription", ""),
                tags=item.get("jobIndustry", []),
                posted_at=item.get("pubDate", ""),
                location=item.get("jobGeo", "Remote"),
            ))
        return jobs


class HimalayasScraper(BaseScraper):
    name = "himalayas"

    async def _scrape_impl(self) -> list[dict]:
        jobs = []
        for offset in (0, 50, 100):
            try:
                data = await self._get_json(
                    f"https://himalayas.app/jobs/api?limit=50&offset={offset}"
                )
            except Exception:
                logger.debug(f"[{self.name}] Offset {offset} failed, stopping pagination")
                break
            page_jobs = data.get("jobs", [])
            if not page_jobs:
                break
            for item in page_jobs:
                if not self._is_today(item.get("publishedDate")):
                    continue
                tags = item.get("categories", [])
                if isinstance(tags, list) and tags and isinstance(tags[0], dict):
                    tags = [t.get("name", "") for t in tags]
                loc_parts = []
                if item.get("timezones"):
                    loc_parts = item["timezones"] if isinstance(item["timezones"], list) else [str(item["timezones"])]

                # Build company URL from companyWebsite or companyName slug
                company_url = item.get("companyWebsite", "") or item.get("website", "")
                if not company_url:
                    cname = item.get("companyName", "")
                    if cname:
                        company_url = f"https://himalayas.app/companies/{cname.lower().replace(' ', '-')}"

                jobs.append(self._job(
                    source_id=item.get("id", ""),
                    url=item.get("applicationLink", ""),
                    title=item.get("title", ""),
                    company_name=item.get("companyName", ""),
                    company_url=company_url,
                    description=item.get("description", ""),
                    tags=tags,
                    posted_at=item.get("publishedDate", ""),
                    location=", ".join(loc_parts) if loc_parts else "Remote",
                ))
        return jobs


class TheMuseScraper(BaseScraper):
    name = "themuse"

    async def _scrape_impl(self) -> list[dict]:
        jobs = []
        for page in range(0, 5):
            try:
                data = await self._get_json(
                    f"https://www.themuse.com/api/public/jobs?category=Software+Engineering&page={page}&descending=true"
                )
            except Exception:
                logger.debug(f"[{self.name}] Page {page} failed, stopping pagination")
                break
            results = data.get("results", data.get("jobs", []))
            if not results:
                break
            for item in results:
                pub_date = item.get("publication_date") or item.get("published_at") or item.get("date")
                if not self._is_today(pub_date):
                    continue
                locations = item.get("locations", [])
                loc_names = []
                for loc in locations:
                    if isinstance(loc, dict):
                        loc_names.append(loc.get("name", ""))
                    elif isinstance(loc, str):
                        loc_names.append(loc)
                is_remote = any("remote" in l.lower() or "flexible" in l.lower() for l in loc_names)
                if locations and not is_remote:
                    continue
                categories = item.get("categories", [])
                tags = []
                if isinstance(categories, list) and categories:
                    if isinstance(categories[0], dict):
                        tags = [c.get("name", "") for c in categories]
                    else:
                        tags = categories
                company = item.get("company", {})
                if isinstance(company, dict):
                    company_name = company.get("name", "")
                elif isinstance(company, str):
                    company_name = company
                else:
                    company_name = str(company)
                title = item.get("name") or item.get("title", "")
                jobs.append(self._job(
                    source_id=item.get("id", ""),
                    url=item.get("refs", {}).get("landing_page", "") if isinstance(item.get("refs"), dict) else item.get("url", ""),
                    title=title,
                    company_name=company_name,
                    description=item.get("contents", item.get("description", "")),
                    tags=tags,
                    posted_at=str(pub_date) if pub_date else "",
                ))
        return jobs


class HNHiringScraper(BaseScraper):
    name = "hn_hiring"
    rate_limit = 0.5

    async def _scrape_impl(self) -> list[dict]:
        # Step 1: Find the latest "Who is hiring?" thread via Algolia search
        try:
            search = await self._get_json(
                "https://hn.algolia.com/api/v1/search_by_date"
                "?query=%22Ask+HN%3A+Who+is+hiring%22"
                "&tags=story,ask_hn&hitsPerPage=5"
            )
        except Exception:
            logger.warning(f"[{self.name}] Algolia search failed")
            return []

        hits = search.get("hits", [])
        thread_id = None
        for hit in hits:
            title = (hit.get("title") or "").lower()
            if "who is hiring" in title:
                thread_id = hit.get("objectID")
                break
        if not thread_id:
            logger.info(f"[{self.name}] No hiring thread found")
            return []

        # Step 2: Fetch thread with all children via Algolia items API
        try:
            thread = await self._get_json(
                f"https://hn.algolia.com/api/v1/items/{thread_id}"
            )
        except Exception:
            logger.warning(f"[{self.name}] Failed to fetch thread {thread_id}")
            return []

        children = thread.get("children", [])
        if not children:
            logger.info(f"[{self.name}] Thread {thread_id} has no children")
            return []

        # Step 3: Parse top-level comments (each is a job posting)
        jobs = []
        for child in children[:300]:
            text = child.get("text", "")
            if not text:
                continue
            if "remote" not in text.lower():
                continue

            created = child.get("created_at") or child.get("created_at_i")
            if not self._is_today(created):
                continue

            job = self._parse_hn_comment(child, text)
            if job:
                jobs.append(job)

        return jobs

    def _parse_hn_comment(self, comment: dict, text: str) -> dict | None:
        # HN hiring posts follow: Company | Role | Location | ...
        clean = re.sub(r"<[^>]+>", " ", text)
        clean = clean.replace("&amp;", "&").replace("&#x27;", "'").replace("&quot;", '"').replace("&#x2F;", "/")

        parts = [p.strip() for p in clean.split("|")]
        company = parts[0] if len(parts) >= 1 else ""
        title = parts[1] if len(parts) >= 2 else ""

        if not company or not title:
            return None

        url_match = re.search(r'href="([^"]+)"', text)
        comment_id = comment.get("id", "")
        job_url = url_match.group(1) if url_match else f"https://news.ycombinator.com/item?id={comment_id}"

        posted_at = comment.get("created_at", "")

        return self._job(
            source_id=str(comment_id),
            url=job_url,
            title=title[:200],
            company_name=company[:100],
            description=clean[:2000],
            posted_at=posted_at,
        )


class GreenhouseScraper(BaseScraper):
    name = "greenhouse"
    rate_limit = 1.0

    COMPANIES = {
        "canonical": ("Canonical", "canonical.com"),
        "vercel": ("Vercel", "vercel.com"),
        "grafanalabs": ("Grafana Labs", "grafana.com"),
        "netlify": ("Netlify", "netlify.com"),
        "circleci": ("CircleCI", "circleci.com"),
        "cockroachlabs": ("CockroachDB", "cockroachlabs.com"),
        "transfergo": ("TransferGo", "transfergo.com"),
        "contentful": ("Contentful", "contentful.com"),
        "postman": ("Postman", "postman.com"),
        "airtable": ("Airtable", "airtable.com"),
        "planetscale": ("PlanetScale", "planetscale.com"),
        "cultureamp": ("Culture Amp", "cultureamp.com"),
        "dbtlabs": ("dbt Labs", "getdbt.com"),
        "lattice": ("Lattice", "lattice.com"),
        "gusto": ("Gusto", "gusto.com"),
        "loom": ("Loom", "loom.com"),
        "webflow": ("Webflow", "webflow.com"),
        "retool": ("Retool", "retool.com"),
    }

    async def _scrape_impl(self) -> list[dict]:
        jobs = []
        for slug, (company_name, domain) in self.COMPANIES.items():
            try:
                resp = await self.client.get(
                    f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
                    timeout=15,
                )
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                data = resp.json()
                await asyncio.sleep(self.rate_limit)
                for item in data.get("jobs", []):
                    location = ""
                    if isinstance(item.get("location"), dict):
                        location = item["location"].get("name", "")
                    elif isinstance(item.get("location"), str):
                        location = item["location"]

                    loc_lower = location.lower()
                    if not any(w in loc_lower for w in ("remote", "worldwide", "anywhere", "global")):
                        continue

                    published = item.get("first_published") or item.get("updated_at")
                    if not self._is_today(published):
                        continue

                    desc = item.get("content", "")
                    if desc:
                        from bs4 import BeautifulSoup as _BS
                        desc = _BS(desc, "lxml").get_text(separator=" ", strip=True)[:2000]

                    departments = item.get("departments", [])
                    tags = [d.get("name", "") for d in departments if isinstance(d, dict)]

                    jobs.append(self._job(
                        source_id=str(item.get("id", "")),
                        url=item.get("absolute_url", ""),
                        title=item.get("title", ""),
                        company_name=company_name,
                        company_url=f"https://{domain}",
                        description=desc,
                        tags=tags,
                        posted_at=str(published) if published else "",
                        location=location or "Remote",
                    ))
            except Exception:
                continue
        return jobs


class LeverScraper(BaseScraper):
    name = "lever"
    rate_limit = 1.0

    COMPANIES = {
        "sonarsource": ("SonarSource", "sonarsource.com"),
        "toptal": ("Toptal", "toptal.com"),
    }

    async def _scrape_impl(self) -> list[dict]:
        jobs = []
        for slug, (company_name, domain) in self.COMPANIES.items():
            try:
                resp = await self.client.get(
                    f"https://api.lever.co/v0/postings/{slug}",
                    timeout=15,
                )
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, list):
                    continue
                await asyncio.sleep(self.rate_limit)
                for item in data:
                    created = item.get("createdAt")
                    if created and not self._is_today(created / 1000):
                        continue

                    cats = item.get("categories", {})
                    location = cats.get("location", "")
                    workplace = item.get("workplaceType", "")

                    desc = item.get("descriptionPlain", "")

                    jobs.append(self._job(
                        source_id=item.get("id", ""),
                        url=item.get("hostedUrl", ""),
                        title=item.get("text", ""),
                        company_name=company_name,
                        company_url=f"https://{domain}",
                        description=desc[:2000],
                        tags=[cats.get("department", ""), cats.get("team", "")],
                        posted_at=str(created) if created else "",
                        location=location or workplace or "Remote",
                    ))
            except Exception:
                continue
        return jobs


class AshbyScraper(BaseScraper):
    name = "ashby"
    rate_limit = 1.0

    COMPANIES = {
        "notion": ("Notion", "notion.so"),
        "ramp": ("Ramp", "ramp.com"),
        "linear": ("Linear", "linear.app"),
        "supabase": ("Supabase", "supabase.com"),
        "vanta": ("Vanta", "vanta.com"),
        "temporal": ("Temporal", "temporal.io"),
        "amplitude": ("Amplitude", "amplitude.com"),
        "benchling": ("Benchling", "benchling.com"),
        "render": ("Render", "render.com"),
        "fly": ("Fly.io", "fly.io"),
        "stytch": ("Stytch", "stytch.com"),
    }

    async def _scrape_impl(self) -> list[dict]:
        jobs = []
        for slug, (company_name, domain) in self.COMPANIES.items():
            try:
                resp = await self.client.get(
                    f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
                    timeout=20,
                )
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                data = resp.json()
                await asyncio.sleep(self.rate_limit)
                for item in data.get("jobs", []):
                    published = item.get("publishedAt", "")
                    if published and not self._is_today(published):
                        continue

                    location = item.get("location", "")
                    is_remote = item.get("isRemote", False)

                    dept = item.get("department", "")
                    team = item.get("team", "")
                    tags = [t for t in [dept, team] if t]

                    desc = item.get("descriptionPlain", "")

                    jobs.append(self._job(
                        source_id=item.get("id", ""),
                        url=item.get("jobUrl", ""),
                        title=item.get("title", ""),
                        company_name=company_name,
                        company_url=f"https://{domain}",
                        description=desc[:2000],
                        tags=tags,
                        posted_at=published,
                        location=location if location else ("Remote" if is_remote else ""),
                    ))
            except Exception:
                continue
        return jobs


def get_all_api_scrapers() -> list[BaseScraper]:
    return [
        RemoteOKScraper(),
        RemotiveScraper(),
        ArbeitnowScraper(),
        JobicyScraper(),
        HimalayasScraper(),
        TheMuseScraper(),
        HNHiringScraper(),
        GreenhouseScraper(),
        LeverScraper(),
        AshbyScraper(),
    ]
