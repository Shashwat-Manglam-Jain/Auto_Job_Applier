"""
Main pipeline entry point.

Usage:
  python run.py                  # run ALL scrapers in one process
  python run.py --pipeline 1     # LinkedIn (heavy, 14 requests)
  python run.py --pipeline 2     # Indeed (rate-limited, 10 requests)
  python run.py --pipeline 3     # Fast APIs (RemoteOK, Arbeitnow, Jobicy)
  python run.py --pipeline 4     # Paginated APIs (Remotive, Himalayas, TheMuse, HN Hiring)
  python run.py --pipeline 5     # HTML boards (JustRemote, NoDesk, 4DayWeek, BuiltIn, DailyRemote)
  python run.py --pipeline 6     # Startup & niche (YC, Wellfound, ArcDev, EURemoteJobs)
  python run.py --pipeline 7     # RSS feeds (WWR, WorkingNomads, Golang, Dribbble, LaraJobs, VueJobs)
  ./run_all.sh                   # launch all 7 pipelines in parallel
  python run.py --report         # scrape-only report (no emails)
  python run.py --manual         # interactive manual send
"""

import asyncio
import random
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
load_dotenv()

from config import (
    PROFILE, SMTP_ACCOUNTS, DAILY_EMAIL_CAP, PER_ACCOUNT_CAP,
    SEND_DELAY_MIN, SEND_DELAY_MAX, MIN_MATCH_CONFIDENCE, MIN_EMAIL_CONFIDENCE,
    INELIGIBLE_KEYWORDS, TARGET_COUNTRIES, TECH_KEYWORDS, MODE,
)
from matcher import match_role, smart_match_role, is_tech_job
from scorer import score_company
from customizer import generate_custom_resume, _extract_job_techs, _format_tech
from sender import compose_email, send_email, send_manual_email, _get_next_account, _reset_counts
from reporter import send_daily_report
from contacts.finder import find_contacts
from resume_templates import get_template

_neon_ok = False
try:
    from neon_db import (init_db, save_company, save_job, save_contact,
                         save_application, mark_company_sent, get_sent_company_names)
    _HAS_NEON = True
except ImportError:
    _HAS_NEON = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("pipeline")

IST = timezone(timedelta(hours=5, minutes=30))

MAX_EMAILS_PER_COMPANY = 5

# ── Pipeline definitions ─────────────────────────────────────────────────
# Each pipeline is a subset of scrapers that runs independently.
# All pipelines share the same Neon DB so dedup is automatic.
# Total email cap across all pipelines: ~1000/day
PIPELINES = {
    "1": {
        "name": "linkedin",
        "scrapers": ["linkedin"],
        "email_cap": 200,
    },
    "2": {
        "name": "indeed",
        "scrapers": ["indeed"],
        "email_cap": 150,
    },
    "3": {
        "name": "fast-apis",
        "scrapers": ["remoteok", "arbeitnow", "jobicy"],
        "email_cap": 150,
    },
    "4": {
        "name": "paginated-apis",
        "scrapers": ["remotive", "himalayas", "themuse", "hn_hiring"],
        "email_cap": 150,
    },
    "5": {
        "name": "html-boards",
        "scrapers": ["justremote", "nodesk", "4dayweek", "builtin", "dailyremote"],
        "email_cap": 120,
    },
    "6": {
        "name": "startup-niche",
        "scrapers": ["ycjobs", "wellfound", "arcdev", "euremotejobs"],
        "email_cap": 120,
    },
    "7": {
        "name": "rss-feeds",
        "scrapers": ["weworkremotely", "workingnomads", "golangjobs",
                      "dribbble", "larajobs", "vuejobs"],
        "email_cap": 110,
    },
}


def _get_all_scrapers():
    scrapers = []
    try:
        from scrapers.api_scrapers import get_all_api_scrapers
        scrapers.extend(get_all_api_scrapers())
    except Exception as e:
        logger.warning("Failed to load API scrapers: %s", e)
    try:
        from scrapers.rss_scrapers import get_all_rss_scrapers
        scrapers.extend(get_all_rss_scrapers())
    except Exception as e:
        logger.warning("Failed to load RSS scrapers: %s", e)
    try:
        from scrapers.html_scrapers import get_all_html_scrapers
        scrapers.extend(get_all_html_scrapers())
    except Exception as e:
        logger.warning("Failed to load HTML scrapers: %s", e)
    return scrapers


def _get_pipeline_scrapers(pipeline_id: str):
    """Load only the scrapers assigned to this pipeline."""
    all_scrapers = _get_all_scrapers()
    cfg = PIPELINES.get(pipeline_id)
    if not cfg:
        logger.error("Unknown pipeline: %s. Valid: %s", pipeline_id,
                      ", ".join(PIPELINES.keys()))
        return [], cfg
    allowed = set(cfg["scrapers"])
    selected = [s for s in all_scrapers if s.name in allowed]
    return selected, cfg


def _is_eligible(job: dict) -> bool:
    text = f"{job.get('title', '')} {job.get('description', '')[:500]} {job.get('location', '')}".lower()
    if MODE == "prod":
        for kw in INELIGIBLE_KEYWORDS:
            if kw in text:
                return False
    location = job.get("location", "").lower()
    if location:
        for country in TARGET_COUNTRIES:
            if country in location:
                return True
    if any(kw in text for kw in ("remote", "worldwide", "anywhere", "global",
                                  "freelance", "contract", "part-time", "part time")):
        return True
    if not location:
        return True
    return False


def _dedup_jobs(jobs: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for job in jobs:
        key = (
            job.get("company_name", "").lower().strip(),
            job.get("title", "").lower().strip(),
        )
        if key not in seen:
            seen.add(key)
            unique.append(job)
    return unique


def _dedup_applications(applications: list[dict]) -> list[dict]:
    seen_emails = set()
    company_counts = {}
    unique = []
    for app in applications:
        email = app.get("contact_email", "").lower().strip()
        if not email or email in seen_emails:
            continue

        company_key = app.get("company_name", "").lower().strip()
        count = company_counts.get(company_key, 0)
        if count >= MAX_EMAILS_PER_COMPANY:
            continue

        seen_emails.add(email)
        company_counts[company_key] = count + 1
        unique.append(app)
    return unique


def _get_top_skills(role_key: str, count: int = 5) -> str:
    template = get_template(role_key)
    if template:
        all_skills = [s for vals in template["skills"].values() for s in vals]
        return ", ".join(all_skills[:count])
    return "modern technologies and tools"


def _get_role_title(role_key: str) -> str:
    template = get_template(role_key)
    if template:
        return template["title"]
    return role_key.replace("_", " ").title() if role_key else "Software Engineer"


_SCRAPER_TIMEOUT = 120

async def _scrape_all(scrapers) -> tuple[list[dict], dict]:
    all_jobs = []
    per_source = {}

    async def _run_one(scraper):
        try:
            jobs = await asyncio.wait_for(scraper.scrape(), timeout=_SCRAPER_TIMEOUT)
            return scraper.name, jobs
        except asyncio.TimeoutError:
            logger.warning("  %s timed out after %ds", scraper.name, _SCRAPER_TIMEOUT)
            return scraper.name, []
        except Exception as e:
            logger.error("  %s failed: %s", scraper.name, e)
            return scraper.name, []

    results = await asyncio.gather(*[_run_one(s) for s in scrapers])
    for name, jobs in results:
        all_jobs.extend(jobs)
        per_source[name] = len(jobs)
        if jobs:
            logger.info("  %s: %d jobs", name, len(jobs))

    return all_jobs, per_source


_PIPELINE_MAX_SECONDS = 6600  # 110 min (buffer before GitHub 2h limit)
_SCRAPE_BUDGET = 0.12         # 12% for scraping
_CONTACT_BUDGET = 0.68        # 68% for contact discovery (main bottleneck)
_SEND_BUDGET = 0.20           # 20% for sending emails


def _time_left(start_time: float) -> float:
    import time
    return max(0, _PIPELINE_MAX_SECONDS - (time.time() - start_time))


async def run_pipeline(pipeline_id: str | None = None):
    import time as _time
    pipeline_start = _time.time()

    email_cap = DAILY_EMAIL_CAP
    pipeline_label = "ALL"
    pipeline_cfg = None
    if pipeline_id:
        _, pipeline_cfg = _get_pipeline_scrapers(pipeline_id)
        if not pipeline_cfg:
            return
        email_cap = pipeline_cfg.get("email_cap", 15)
        pipeline_label = f"{pipeline_id} ({pipeline_cfg['name']})"

    logger.info("=" * 60)
    logger.info("Starting Auto-Apply Pipeline [%s] — %s",
                pipeline_label, datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"))
    logger.info("Mode: %s | Email cap: %d | SMTP accounts: %d | Time budget: %dmin",
                MODE.upper(), email_cap, len(SMTP_ACCOUNTS),
                _PIPELINE_MAX_SECONDS // 60)
    logger.info("=" * 60)

    if not SMTP_ACCOUNTS:
        logger.error("No SMTP accounts configured. Set SMTP_ACCOUNT_1/SMTP_PASSWORD_1 in .env")
        return

    global _neon_ok
    if _HAS_NEON:
        try:
            init_db()
            _neon_ok = True
            logger.info("Neon DB initialized")
        except Exception as e:
            logger.warning("Neon DB init failed, continuing without DB: %s", e)
            _neon_ok = False

    _reset_counts()
    stats = {
        "total_scraped": 0, "today_jobs": 0, "filtered_jobs": 0,
        "unique_jobs": 0, "matched_jobs": 0, "contacts_found": 0,
        "emails_attempted": 0, "emails_sent": 0, "emails_failed": 0,
        "per_source": {}, "per_account": {},
    }
    all_jobs = []
    applications = []
    sent_results = []
    report_sent = False

    try:
        # Step 1: Scrape (budget: 12%)
        scrape_deadline = pipeline_start + _PIPELINE_MAX_SECONDS * _SCRAPE_BUDGET
        logger.info("\n[Step 1/5] Scraping platforms (budget: %dmin)...",
                    int(_PIPELINE_MAX_SECONDS * _SCRAPE_BUDGET / 60))
        if pipeline_id:
            scrapers, _ = _get_pipeline_scrapers(pipeline_id)
        else:
            scrapers = _get_all_scrapers()
        logger.info("Loaded %d scrapers for pipeline [%s]", len(scrapers), pipeline_label)
        all_jobs, per_source = await _scrape_all(scrapers)
        stats["total_scraped"] = len(all_jobs)
        stats["per_source"] = per_source
        logger.info("Total jobs scraped: %d (%.0fs elapsed)",
                    len(all_jobs), _time.time() - pipeline_start)

        if not all_jobs:
            logger.warning("No jobs scraped.")
            return

        # Step 2: Filter + Dedup + Match + Score (fast, <10s)
        logger.info("\n[Step 2/5] Filter → Dedup → Match → Score...")
        filtered = []
        for job in all_jobs:
            title = job.get("title", "").strip()
            company = job.get("company_name", "").strip()
            if not title or not company:
                continue
            if title.lower().startswith("list ") or len(title) < 5:
                continue
            if not is_tech_job(title, job.get("tags", [])):
                continue
            if not _is_eligible(job):
                continue
            filtered.append(job)

        unique_jobs = _dedup_jobs(filtered)

        sent_names = set()
        if _neon_ok:
            try:
                sent_names = get_sent_company_names()
                if sent_names:
                    before = len(unique_jobs)
                    unique_jobs = [j for j in unique_jobs
                                   if j.get("company_name", "").lower().strip() not in sent_names]
                    logger.info("Skipped %d already-contacted companies", before - len(unique_jobs))
            except Exception as e:
                logger.debug("sent_names lookup failed: %s", e)

        for job in unique_jobs:
            role_key, confidence = smart_match_role(job)
            job["role_key"] = role_key
            job["match_confidence"] = confidence
        matched = [j for j in unique_jobs if j.get("role_key") and
                   j["match_confidence"] >= MIN_MATCH_CONFIDENCE]
        for job in matched:
            job["company_score"] = score_company(job)
        matched.sort(key=lambda j: j["company_score"], reverse=True)

        stats["today_jobs"] = len(all_jobs)
        stats["filtered_jobs"] = len(filtered)
        stats["unique_jobs"] = len(unique_jobs)
        stats["matched_jobs"] = len(matched)
        logger.info("Filtered: %d | Unique: %d | Matched: %d",
                    len(filtered), len(unique_jobs), len(matched))

        if _neon_ok:
            logger.info("Saving %d matched jobs to Neon DB...", len(matched))
            from contacts.email_guesser import get_domain_from_url
            db_saved = 0
            for job in matched:
                try:
                    domain = get_domain_from_url(job.get("company_url", ""))
                    company_id = save_company(
                        name=job.get("company_name", ""),
                        domain=domain,
                        url=job.get("company_url", ""),
                    )
                    if company_id:
                        job["_db_company_id"] = company_id
                        job_id = save_job(
                            company_id=company_id,
                            source=job.get("source", ""),
                            source_id=job.get("source_id", ""),
                            title=job.get("title", ""),
                            url=job.get("url", ""),
                            description=job.get("description", ""),
                            tags=job.get("tags", []),
                            location=job.get("location", ""),
                            salary_min=job.get("salary_min"),
                            salary_max=job.get("salary_max"),
                            role_key=job.get("role_key", ""),
                            match_confidence=job.get("match_confidence", 0),
                            posted_at=job.get("posted_at", ""),
                        )
                        if job_id:
                            job["_db_job_id"] = job_id
                            db_saved += 1
                except Exception as e:
                    logger.debug("DB save failed for %s: %s", job.get("company_name"), e)
            logger.info("Saved %d/%d jobs to Neon DB", db_saved, len(matched))

        # Step 3: Find contacts (budget: 55%)
        contact_deadline = pipeline_start + _PIPELINE_MAX_SECONDS * (_SCRAPE_BUDGET + _CONTACT_BUDGET)
        logger.info("\n[Step 3/5] Discovering contacts (budget: %dmin)...",
                    int(_PIPELINE_MAX_SECONDS * _CONTACT_BUDGET / 60))

        companies_processed = set()
        unique_company_jobs = []
        for job in matched:
            company_key = job.get("company_name", "").lower().strip()
            if company_key not in companies_processed:
                companies_processed.add(company_key)
                unique_company_jobs.append(job)

        _CONTACT_BATCH = 24

        async def _find_for_job(job):
            try:
                contacts = await find_contacts(
                    job.get("company_name", ""),
                    job.get("company_url", ""),
                    job_description=job.get("description", ""),
                )
                results = []
                for contact in contacts:
                    if contact.get("confidence", 0) >= MIN_EMAIL_CONFIDENCE:
                        results.append({
                            **job,
                            "contact_email": contact["email"],
                            "contact_name": contact.get("name", ""),
                            "contact_title": contact.get("title", ""),
                            "contact_confidence": contact["confidence"],
                        })
                return results
            except Exception as e:
                logger.warning("Contact discovery failed for %s: %s",
                              job.get("company_name", "?"), e)
                return []

        for batch_start in range(0, len(unique_company_jobs), _CONTACT_BATCH):
            if len(applications) >= email_cap:
                break
            if _time.time() > contact_deadline:
                logger.warning("Contact discovery time budget exceeded — moving to send phase")
                break
            batch = unique_company_jobs[batch_start:batch_start + _CONTACT_BATCH]
            batch_results = await asyncio.gather(*[_find_for_job(j) for j in batch])
            for result_list in batch_results:
                applications.extend(result_list)

        applications = _dedup_applications(applications)
        applications.sort(key=lambda a: a.get("contact_confidence", 0), reverse=True)
        applications = applications[:email_cap]
        stats["contacts_found"] = len(applications)

        if _neon_ok:
            for app in applications:
                try:
                    company_id = app.get("_db_company_id")
                    if not company_id:
                        for job in matched:
                            if job.get("company_name", "").lower() == app.get("company_name", "").lower():
                                company_id = job.get("_db_company_id")
                                break
                    if company_id:
                        contact_id = save_contact(
                            company_id=company_id,
                            email=app.get("contact_email", ""),
                            name=app.get("contact_name", ""),
                            title=app.get("contact_title", ""),
                            confidence=app.get("contact_confidence", 0),
                            source="auto_discovery",
                            verified=True,
                        )
                        if contact_id:
                            app["_db_contact_id"] = contact_id
                except Exception as e:
                    logger.debug("DB contact save failed: %s", e)

        logger.info("Applications ready: %d verified emails (%.0fs elapsed)",
                    len(applications), _time.time() - pipeline_start)

        # Step 4: Send report ONCE with all job/company data
        logger.info("\n[Step 4/5] Sending report...")
        try:
            send_daily_report(stats, [], applications, all_jobs)
            report_sent = True
            logger.info("Report sent!")
        except Exception as e:
            logger.error("Failed to send report: %s", e)

        # Step 5: Send emails (budget: 30%)
        if not applications:
            logger.info("No applications to send.")
        else:
            send_deadline = pipeline_start + _PIPELINE_MAX_SECONDS
            logger.info("\n[Step 5/5] Sending %d emails (budget: %dmin)...",
                        len(applications),
                        int(_PIPELINE_MAX_SECONDS * _SEND_BUDGET / 60))
            consecutive_bounces = 0
            MAX_CONSECUTIVE_BOUNCES = 5

            for i, app in enumerate(applications):
                if _time.time() > send_deadline - 60:
                    logger.warning("Time budget almost up — stopping sends at %d/%d",
                                   i, len(applications))
                    break

                if consecutive_bounces >= MAX_CONSECUTIVE_BOUNCES:
                    logger.warning("Stopping sends — %d consecutive bounces.", consecutive_bounces)
                    break

                smtp_account = _get_next_account(i)
                if not smtp_account:
                    logger.warning("All SMTP accounts reached daily cap at email %d", i)
                    break

                role_key = app.get("role_key", "software_engineer")
                role_title = _get_role_title(role_key)
                company_name = app.get("company_name", "")
                hr_name = app.get("contact_name", "")
                actual_job_title = app.get("title", "")

                job_techs = _extract_job_techs(
                    app.get("description", ""), app.get("tags", [])
                )
                if job_techs:
                    top_skills = ", ".join(_format_tech(t) for t in job_techs[:5])
                else:
                    top_skills = _get_top_skills(role_key)

                job_text = f"{actual_job_title} {app.get('description', '')[:200]}".lower()
                if any(kw in job_text for kw in ("freelance", "contract", "contractor", "part-time", "part time")):
                    template_key = "freelance"
                elif app.get("url"):
                    template_key = "job_apply"
                else:
                    template_key = "cold_outreach"
                subject, body = compose_email(
                    hr_name=hr_name,
                    company_name=company_name,
                    role_title=role_title,
                    top_skills=top_skills,
                    template_key=template_key,
                    job_title=actual_job_title,
                )

                try:
                    pdf_bytes = generate_custom_resume(
                        role_key,
                        app.get("tags", []),
                        app.get("description", ""),
                        job_title=app.get("title", ""),
                        company_name=company_name,
                    )
                except Exception as e:
                    logger.warning("Custom resume failed for %s: %s", company_name, e)
                    from resume_templates import generate_pdf_resume
                    pdf_bytes = generate_pdf_resume(role_key, PROFILE)

                name_slug = PROFILE.get("name", "Resume").replace(" ", "_")
                role_slug = role_title.replace(" ", "_")
                pdf_filename = f"{name_slug}_{role_slug}_Resume.pdf"

                result = send_email(
                    to_email=app["contact_email"],
                    subject=subject,
                    body=body,
                    pdf_bytes=pdf_bytes,
                    pdf_filename=pdf_filename,
                    smtp_account=smtp_account,
                    cc_emails=app.get("cc_emails"),
                    validate=True,
                )
                result["company_name"] = company_name
                result["role_title"] = role_title
                sent_results.append(result)

                if _neon_ok:
                    try:
                        db_job_id = app.get("_db_job_id")
                        db_contact_id = app.get("_db_contact_id")
                        if db_job_id:
                            save_application(
                                job_id=db_job_id,
                                contact_id=db_contact_id,
                                status=result["status"],
                                sent_at=datetime.now(timezone.utc).isoformat() if result["status"] == "sent" else None,
                                sent_via=result.get("via", ""),
                                error=result.get("error", ""),
                            )
                    except Exception as e:
                        logger.debug("DB application save failed: %s", e)

                if result["status"] == "sent":
                    consecutive_bounces = 0
                    if _neon_ok:
                        try:
                            mark_company_sent(company_name, app["contact_email"], smtp_account["email"])
                        except Exception:
                            pass
                    logger.info("  [%d/%d] Sent to %s (%s) via %s",
                               i + 1, len(applications), app["contact_email"],
                               company_name, smtp_account["email"])
                elif result["status"] == "skipped":
                    logger.info("  [%d/%d] Skipped %s: %s",
                               i + 1, len(applications), app["contact_email"],
                               result.get("error", ""))
                else:
                    error = result.get("error", "").lower()
                    if "does not exist" in error or "550" in error or "user unknown" in error:
                        consecutive_bounces += 1
                    else:
                        consecutive_bounces = 0
                    logger.warning("  [%d/%d] Failed %s: %s",
                                  i + 1, len(applications), app["contact_email"],
                                  result.get("error", ""))

                if result["status"] != "skipped":
                    delay = random.uniform(SEND_DELAY_MIN, SEND_DELAY_MAX)
                    await asyncio.sleep(delay)

    except Exception as e:
        logger.error("Pipeline error: %s", e, exc_info=True)
    finally:
        elapsed = _time.time() - pipeline_start
        sent_ok = [r for r in sent_results if r.get("status") == "sent"]
        sent_fail = [r for r in sent_results if r.get("status") == "failed"]
        sent_skip = [r for r in sent_results if r.get("status") == "skipped"]
        stats["emails_attempted"] = len(sent_results)
        stats["emails_sent"] = len(sent_ok)
        stats["emails_failed"] = len(sent_fail)
        stats["emails_skipped"] = len(sent_skip)

        per_account = {}
        for r in sent_ok:
            via = r.get("via", "unknown")
            per_account[via] = per_account.get(via, 0) + 1
        stats["per_account"] = per_account

        logger.info("\n" + "=" * 60)
        logger.info("Pipeline Complete! (%.0fs / %dmin)", elapsed, int(elapsed / 60))
        logger.info("  Sent: %d | Failed: %d | Skipped: %d | Scraped: %d",
                   len(sent_ok), len(sent_fail), len(sent_skip),
                   stats["total_scraped"])
        logger.info("=" * 60)


def _print_scrape_report(all_jobs: list[dict], per_source: dict,
                         filtered: list[dict], matched: list[dict]):
    now = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
    print(f"\n{'=' * 100}")
    print(f"  DAILY SCRAPE REPORT — {now}  |  Mode: {MODE.upper()}")
    print(f"{'=' * 100}")

    print(f"\n  Total scraped: {len(all_jobs)}  |  After filter: {len(filtered)}  |  Matched: {len(matched)}")

    print(f"\n  {'Platform':<25} {'Jobs':>6}")
    print(f"  {'-' * 25} {'-' * 6}")
    for source, count in sorted(per_source.items(), key=lambda x: -x[1]):
        if count > 0:
            print(f"  {source:<25} {count:>6}")

    print(f"\n  {'#':<5} {'Source':<18} {'Company':<22} {'Title':<30} {'Location':<20} {'Tags'}")
    print(f"  {'-' * 5} {'-' * 18} {'-' * 22} {'-' * 30} {'-' * 20} {'-' * 20}")
    for i, job in enumerate(filtered[:100], 1):
        source = job.get("source", "?")[:17]
        company = job.get("company_name", "?")[:21]
        title = job.get("title", "?")[:29]
        location = (job.get("location", "") or "Remote")[:19]
        tags = ", ".join(job.get("tags", [])[:3])[:20]
        print(f"  {i:<5} {source:<18} {company:<22} {title:<30} {location:<20} {tags}")
    if len(filtered) > 100:
        print(f"\n  ... and {len(filtered) - 100} more jobs (showing top 100)")

    print(f"\n{'=' * 100}\n")


async def run_scrape_report():
    logger.info("Running scrape-only report (no emails)...")
    scrapers = _get_all_scrapers()
    logger.info("Loaded %d scrapers", len(scrapers))
    all_jobs, per_source = await _scrape_all(scrapers)

    filtered = []
    for job in all_jobs:
        title = job.get("title", "").strip()
        company = job.get("company_name", "").strip()
        if not title or not company:
            continue
        if title.lower().startswith("list ") or len(title) < 5:
            continue
        if not is_tech_job(title, job.get("tags", [])):
            continue
        if not _is_eligible(job):
            continue
        filtered.append(job)

    unique_jobs = _dedup_jobs(filtered)

    for job in unique_jobs:
        role_key, confidence = smart_match_role(job)
        job["role_key"] = role_key
        job["match_confidence"] = confidence
    matched = [j for j in unique_jobs if j.get("role_key") and
               j["match_confidence"] >= MIN_MATCH_CONFIDENCE]

    _print_scrape_report(all_jobs, per_source, unique_jobs, matched)
    return all_jobs, unique_jobs, matched


def run_manual_send():
    """Interactive manual email sender with full validation."""
    from config import SMTP_ACCOUNTS

    print(f"\n{'='*60}")
    print("  MANUAL EMAIL SENDER")
    print(f"{'='*60}")

    if not SMTP_ACCOUNTS:
        print("\n  ERROR: No SMTP accounts configured in .env")
        print("  Set SMTP_ACCOUNT_1 and SMTP_PASSWORD_1")
        return

    print(f"\n  Available SMTP accounts:")
    for i, acc in enumerate(SMTP_ACCOUNTS, 1):
        print(f"    {i}. {acc['email']}")

    if len(SMTP_ACCOUNTS) > 1:
        choice = input(f"\n  Select account (1-{len(SMTP_ACCOUNTS)}) [1]: ").strip()
        idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(SMTP_ACCOUNTS) else 0
    else:
        idx = 0
    smtp_account = SMTP_ACCOUNTS[idx]
    print(f"  Using: {smtp_account['email']}")

    to_email = input("\n  To: ").strip()
    if not to_email:
        print("  ERROR: To address is required")
        return

    cc_input = input("  CC (comma-separated, or empty): ").strip()
    cc_emails = [c.strip() for c in cc_input.split(",") if c.strip()] if cc_input else []

    subject = input("  Subject: ").strip()
    if not subject:
        print("  ERROR: Subject is required")
        return

    print("  Body (type your message, then enter a blank line to finish):")
    body_lines = []
    while True:
        line = input("  ")
        if line == "":
            break
        body_lines.append(line)
    body = "\n".join(body_lines)

    if not body:
        print("  ERROR: Body is required")
        return

    attach_path = input("  Attachment path (or empty for none): ").strip()
    attachment_bytes = None
    attachment_filename = None
    if attach_path:
        try:
            with open(attach_path, "rb") as f:
                attachment_bytes = f.read()
            attachment_filename = os.path.basename(attach_path)
            print(f"  Attached: {attachment_filename} ({len(attachment_bytes)} bytes)")
        except FileNotFoundError:
            print(f"  WARNING: File not found: {attach_path} — sending without attachment")
        except Exception as e:
            print(f"  WARNING: Could not read file: {e} — sending without attachment")

    print(f"\n{'='*60}")
    print("  REVIEW")
    print(f"{'='*60}")
    print(f"  From:    {smtp_account['email']}")
    print(f"  To:      {to_email}")
    if cc_emails:
        print(f"  CC:      {', '.join(cc_emails)}")
    print(f"  Subject: {subject}")
    print(f"  Body:    {body[:100]}{'...' if len(body) > 100 else ''}")
    if attachment_filename:
        print(f"  Attach:  {attachment_filename}")

    confirm = input("\n  Send? (y/n) [n]: ").strip().lower()
    if confirm != "y":
        print("  Cancelled.")
        return

    result = send_manual_email(
        to_email=to_email,
        subject=subject,
        body=body,
        cc_emails=cc_emails,
        attachment_bytes=attachment_bytes,
        attachment_filename=attachment_filename,
        smtp_account=smtp_account,
    )

    print(f"\n{'='*60}")
    if result["status"] == "sent":
        print(f"  Email sent successfully via {result['via']}")
        if result.get("cc"):
            print(f"  CC delivered to: {', '.join(result['cc'])}")
        if result.get("skipped_cc"):
            print(f"  CC skipped (invalid):")
            for s in result["skipped_cc"]:
                print(f"    - {s['email']}: {s['reason']}")
    else:
        print(f"  FAILED: {result.get('error', 'unknown error')}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    if "--report" in sys.argv:
        asyncio.run(run_scrape_report())
    elif "--manual" in sys.argv:
        run_manual_send()
    elif "--pipeline" in sys.argv:
        idx = sys.argv.index("--pipeline")
        pid = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None
        if not pid or pid not in PIPELINES:
            print(f"Usage: python run.py --pipeline <1-{len(PIPELINES)}>")
            print("Available pipelines:")
            for k, v in PIPELINES.items():
                print(f"  {k}: {v['name']} — {', '.join(v['scrapers'])}")
            sys.exit(1)
        asyncio.run(run_pipeline(pipeline_id=pid))
    elif "--all" in sys.argv:
        import subprocess
        procs = []
        for pid in PIPELINES:
            p = subprocess.Popen(
                [sys.executable, __file__, "--pipeline", pid],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            procs.append((pid, p))
            logger.info("Launched pipeline %s (PID %d)", pid, p.pid)
        for pid, p in procs:
            out, _ = p.communicate()
            logger.info("Pipeline %s finished (exit %d)", pid, p.returncode)
            if out:
                for line in out.strip().split("\n")[-5:]:
                    logger.info("  [%s] %s", pid, line)
    else:
        asyncio.run(run_pipeline())
