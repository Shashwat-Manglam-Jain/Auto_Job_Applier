"""
Main pipeline entry point.
Run: python run.py
Or via GitHub Actions cron.
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
from customizer import generate_custom_resume
from sender import compose_email, send_email, _get_next_account, _reset_counts
from reporter import send_daily_report
from contacts.finder import find_contacts
from resume_templates import get_template

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("pipeline")

IST = timezone(timedelta(hours=5, minutes=30))


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
    seen_company_role = set()
    unique = []
    for app in applications:
        email = app.get("contact_email", "").lower().strip()
        company_role = (
            app.get("company_name", "").lower().strip(),
            app.get("role_key", "").lower().strip(),
            email,
        )
        if email and email not in seen_emails and company_role not in seen_company_role:
            seen_emails.add(email)
            seen_company_role.add(company_role)
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


async def _scrape_all(scrapers) -> tuple[list[dict], dict]:
    all_jobs = []
    per_source = {}

    for scraper in scrapers:
        try:
            logger.info("Scraping %s...", scraper.name)
            jobs = await scraper.scrape()
            all_jobs.extend(jobs)
            per_source[scraper.name] = len(jobs)
            logger.info("  %s: %d jobs found", scraper.name, len(jobs))
        except Exception as e:
            logger.error("  %s failed: %s", scraper.name, e)
            per_source[scraper.name] = 0

    return all_jobs, per_source


async def run_pipeline():
    logger.info("=" * 60)
    logger.info("Starting Auto-Apply Pipeline — %s",
                datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"))
    logger.info("Mode: %s | Email cap: %d | SMTP accounts: %d",
                MODE.upper(), DAILY_EMAIL_CAP, len(SMTP_ACCOUNTS))
    logger.info("=" * 60)

    if not SMTP_ACCOUNTS:
        logger.error("No SMTP accounts configured. Set SMTP_ACCOUNT_1/SMTP_PASSWORD_1 in .env")
        return

    _reset_counts()
    stats = {}

    # Step 1: Scrape
    logger.info("\n[Step 1/7] Scraping all platforms...")
    scrapers = _get_all_scrapers()
    logger.info("Loaded %d scrapers", len(scrapers))
    all_jobs, per_source = await _scrape_all(scrapers)
    stats["total_scraped"] = len(all_jobs)
    stats["per_source"] = per_source
    logger.info("Total jobs scraped: %d", len(all_jobs))

    if not all_jobs:
        logger.warning("No jobs scraped. Check internet connection and scraper configs.")
        stats.update({"today_jobs": 0, "filtered_jobs": 0, "unique_jobs": 0,
                      "matched_jobs": 0, "contacts_found": 0,
                      "emails_attempted": 0, "emails_sent": 0, "emails_failed": 0})
        send_daily_report(stats, [], [], [])
        return

    # Step 2: Filter
    logger.info("\n[Step 2/7] Filtering for tech + remote + target countries...")
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
    stats["today_jobs"] = len(all_jobs)
    stats["filtered_jobs"] = len(filtered)
    logger.info("After filtering: %d jobs", len(filtered))

    # Step 3: Dedup
    logger.info("\n[Step 3/7] Deduplicating...")
    unique_jobs = _dedup_jobs(filtered)
    stats["unique_jobs"] = len(unique_jobs)
    logger.info("After dedup: %d unique jobs", len(unique_jobs))

    # Step 4: Match roles
    logger.info("\n[Step 4/7] Matching to resume templates...")
    for job in unique_jobs:
        role_key, confidence = smart_match_role(job)
        job["role_key"] = role_key
        job["match_confidence"] = confidence
    matched = [j for j in unique_jobs if j.get("role_key") and
               j["match_confidence"] >= MIN_MATCH_CONFIDENCE]
    stats["matched_jobs"] = len(matched)
    logger.info("Matched to templates: %d jobs", len(matched))

    # Step 5: Score and sort
    logger.info("\n[Step 5/7] Scoring companies...")
    for job in matched:
        job["company_score"] = score_company(job)
    matched.sort(key=lambda j: j["company_score"], reverse=True)

    # Step 6: Find contacts and build application list
    logger.info("\n[Step 6/7] Discovering contacts...")
    applications = []
    companies_processed = set()

    unique_company_jobs = []
    for job in matched:
        company_key = job.get("company_name", "").lower().strip()
        if company_key not in companies_processed:
            companies_processed.add(company_key)
            unique_company_jobs.append(job)

    _CONTACT_BATCH = 5

    async def _find_for_job(job):
        try:
            contacts = await find_contacts(
                job.get("company_name", ""),
                job.get("company_url", ""),
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
        if len(applications) >= DAILY_EMAIL_CAP:
            break
        batch = unique_company_jobs[batch_start:batch_start + _CONTACT_BATCH]
        batch_results = await asyncio.gather(*[_find_for_job(j) for j in batch])
        for result_list in batch_results:
            applications.extend(result_list)

    applications = _dedup_applications(applications)
    applications = applications[:DAILY_EMAIL_CAP]
    stats["contacts_found"] = len(applications)
    logger.info("Total applications to send: %d", len(applications))

    # Step 7: Send emails
    logger.info("\n[Step 7/7] Sending emails...")
    sent_results = []

    for i, app in enumerate(applications):
        smtp_account = _get_next_account(i)
        if not smtp_account:
            logger.warning("All SMTP accounts reached daily cap at email %d", i)
            break

        role_key = app.get("role_key", "software_engineer")
        role_title = _get_role_title(role_key)
        top_skills = _get_top_skills(role_key)
        company_name = app.get("company_name", "")
        hr_name = app.get("contact_name", "")

        job_text = f"{app.get('title', '')} {app.get('description', '')[:200]}".lower()
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
            logger.warning("Custom resume failed for %s, using template: %s", company_name, e)
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
        )
        result["company_name"] = company_name
        result["role_title"] = role_title
        sent_results.append(result)

        if result["status"] == "sent":
            logger.info("  [%d/%d] ✓ Sent to %s (%s) via %s",
                       i + 1, len(applications), app["contact_email"],
                       company_name, smtp_account["email"])
        else:
            logger.warning("  [%d/%d] ✗ Failed %s: %s",
                          i + 1, len(applications), app["contact_email"],
                          result.get("error", ""))

        delay = random.uniform(SEND_DELAY_MIN, SEND_DELAY_MAX)
        await asyncio.sleep(delay)

    # Stats
    sent_ok = [r for r in sent_results if r["status"] == "sent"]
    sent_fail = [r for r in sent_results if r["status"] == "failed"]
    stats["emails_attempted"] = len(sent_results)
    stats["emails_sent"] = len(sent_ok)
    stats["emails_failed"] = len(sent_fail)

    per_account = {}
    for r in sent_ok:
        via = r.get("via", "unknown")
        per_account[via] = per_account.get(via, 0) + 1
    stats["per_account"] = per_account

    logger.info("\n" + "=" * 60)
    logger.info("Pipeline Complete!")
    logger.info("  Sent: %d | Failed: %d | Total scraped: %d",
               len(sent_ok), len(sent_fail), stats["total_scraped"])
    logger.info("=" * 60)

    # Send report with full scraped jobs table
    logger.info("\nSending daily report email...")
    send_daily_report(stats, sent_results, applications, all_jobs)
    logger.info("Done!")


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


if __name__ == "__main__":
    if "--report" in sys.argv:
        asyncio.run(run_scrape_report())
    else:
        asyncio.run(run_pipeline())
