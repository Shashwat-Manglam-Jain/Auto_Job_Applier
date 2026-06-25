"""
Test script for the auto-apply pipeline.
Run individual components or the full pipeline with verbose logging.

Usage:
    python test_pipeline.py scrapers     # Test all scrapers
    python test_pipeline.py contacts     # Test contact discovery
    python test_pipeline.py resume       # Test dynamic resume generation
    python test_pipeline.py apis         # Test API keys
    python test_pipeline.py full         # Full pipeline (DAILY_EMAIL_CAP=3)
    python test_pipeline.py all          # Run all tests
"""

import asyncio
import sys
import os
import logging
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("test")


async def test_scrapers():
    print("\n" + "=" * 70)
    print("SCRAPER TEST — Testing all scrapers individually")
    print("=" * 70)

    from scrapers.api_scrapers import get_all_api_scrapers
    from scrapers.rss_scrapers import get_all_rss_scrapers
    from scrapers.html_scrapers import get_all_html_scrapers

    all_scrapers = get_all_api_scrapers() + get_all_rss_scrapers() + get_all_html_scrapers()

    results = {"working": [], "empty": [], "error": []}
    total_jobs = 0

    for s in all_scrapers:
        start = time.time()
        try:
            jobs = await s.scrape()
            elapsed = time.time() - start
            total_jobs += len(jobs)

            if jobs:
                results["working"].append(s.name)
                j = jobs[0]
                print(f"  OK  {s.name:25s} {len(jobs):4d} jobs  ({elapsed:.1f}s)")
                print(f"       Sample: \"{j['title'][:50]}\" @ {j['company_name'][:25]}")
                if len(j.get("tags", [])) > 0:
                    print(f"       Tags: {', '.join(j['tags'][:5])}")
            else:
                results["empty"].append(s.name)
                print(f"  --  {s.name:25s}    0 jobs  ({elapsed:.1f}s)")
        except Exception as e:
            elapsed = time.time() - start
            results["error"].append((s.name, str(e)[:80]))
            print(f"  ERR {s.name:25s} FAILED ({elapsed:.1f}s): {str(e)[:80]}")

    print(f"\n{'=' * 70}")
    print(f"SCRAPER SUMMARY")
    print(f"  Working: {len(results['working'])}/{len(all_scrapers)} scrapers")
    print(f"  Total jobs: {total_jobs}")
    print(f"  Working: {', '.join(results['working'])}")
    if results["empty"]:
        print(f"  Empty (0 jobs): {', '.join(results['empty'])}")
    if results["error"]:
        print(f"  Errors:")
        for name, err in results["error"]:
            print(f"    {name}: {err}")
    print(f"{'=' * 70}")
    return total_jobs


async def test_contacts():
    print("\n" + "=" * 70)
    print("CONTACT DISCOVERY TEST")
    print("=" * 70)

    from contacts.finder import find_contacts

    test_companies = [
        ("Vercel", "https://vercel.com"),
        ("Stripe", "https://stripe.com"),
        ("Fly.io", "https://fly.io"),
    ]

    for name, url in test_companies:
        print(f"\n--- {name} ({url}) ---")
        start = time.time()
        try:
            contacts = await find_contacts(name, url)
            elapsed = time.time() - start
            print(f"  Found {len(contacts)} contacts in {elapsed:.1f}s:")
            for c in contacts[:5]:
                email = c.get("email", "")
                cname = c.get("name", "")
                title = c.get("title", "")
                conf = c.get("confidence", 0)
                print(f"    {email:35s} conf={conf:.2f}  {cname:20s} {title}")
        except Exception as e:
            print(f"  ERROR: {e}")


def test_resume():
    print("\n" + "=" * 70)
    print("DYNAMIC RESUME TEST")
    print("=" * 70)

    from customizer import generate_custom_resume
    import subprocess

    test_cases = [
        {
            "role": "backend_engineer",
            "tags": ["python", "django", "postgresql", "redis", "docker"],
            "desc": "Senior backend engineer to build microservices with Python.",
            "title": "Senior Backend Engineer",
        },
        {
            "role": "frontend_engineer",
            "tags": ["react", "typescript", "nextjs", "tailwind"],
            "desc": "Frontend engineer for SaaS dashboard with React.",
            "title": "Frontend Engineer - SaaS",
        },
        {
            "role": "devops_engineer",
            "tags": ["kubernetes", "terraform", "aws", "ci/cd", "docker"],
            "desc": "DevOps engineer for cloud infrastructure on AWS.",
            "title": "DevOps Engineer - Cloud",
        },
    ]

    pdfs = []
    for tc in test_cases:
        start = time.time()
        try:
            pdf = generate_custom_resume(tc["role"], tc["tags"], tc["desc"], tc["title"])
            elapsed = time.time() - start
            fname = f"/tmp/test_{tc['role']}.pdf"
            with open(fname, "wb") as f:
                f.write(pdf)

            result = subprocess.run(
                ["pdftotext", fname, "-"], capture_output=True, text=True
            )
            text = result.stdout if result.returncode == 0 else ""

            summary_start = text.find("PROFESSIONAL SUMMARY")
            summary_end = text.find("TECHNICAL SKILLS")
            summary = text[summary_start:summary_end].strip() if summary_start > 0 else "N/A"

            projects_start = text.find("PROJECTS")
            projects_end = text.find("CERTIFICATIONS") if "CERTIFICATIONS" in text else len(text)
            projects = text[projects_start:projects_end].strip()[:300] if projects_start > 0 else "N/A"

            print(f"\n--- {tc['title']} ({tc['role']}) ---")
            print(f"  PDF size: {len(pdf)} bytes, generated in {elapsed:.1f}s")
            print(f"  Summary: {summary[:200]}")
            print(f"  Projects (first 200 chars): {projects[:200]}")
            pdfs.append(pdf)
        except Exception as e:
            print(f"  ERROR generating {tc['title']}: {e}")

    if len(pdfs) >= 2:
        if pdfs[0] != pdfs[1]:
            print("\n  PASS: Resumes are unique per job (different content)")
        else:
            print("\n  FAIL: Resumes are identical — customization not working!")


def test_apis():
    print("\n" + "=" * 70)
    print("API KEY VERIFICATION")
    print("=" * 70)

    import httpx

    groq_key = os.getenv("GROQ_API_KEY", "")
    google_key = os.getenv("GOOGLE_API_KEY", "")
    google_cse = os.getenv("GOOGLE_CSE_ID", "")

    # Groq
    print("\n--- Groq API (AI for resume customization + role matching) ---")
    if groq_key:
        try:
            resp = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json={"model": "llama-3.1-8b-instant",
                      "messages": [{"role": "user", "content": "Say OK"}],
                      "max_tokens": 5},
                timeout=10,
            )
            if resp.status_code == 200:
                print(f"  STATUS: WORKING")
                print(f"  Used for: AI resume summaries, project generation, smart role matching")
            else:
                print(f"  STATUS: ERROR ({resp.status_code}): {resp.text[:100]}")
        except Exception as e:
            print(f"  STATUS: ERROR: {e}")
    else:
        print("  STATUS: NOT SET (set GROQ_API_KEY in .env)")

    # Google Search
    print("\n--- Google Custom Search API (optional contact discovery) ---")
    if google_key and google_cse:
        try:
            resp = httpx.get(
                "https://www.googleapis.com/customsearch/v1",
                params={"key": google_key, "cx": google_cse, "q": "test", "num": 1},
                timeout=10,
            )
            if resp.status_code == 200:
                print(f"  STATUS: WORKING")
            else:
                print(f"  STATUS: ERROR ({resp.status_code})")
                print(f"  Fix: Enable 'Custom Search API' at https://console.cloud.google.com/apis")
        except Exception as e:
            print(f"  STATUS: ERROR: {e}")
    elif google_key:
        print("  STATUS: GOOGLE_CSE_ID not set")
        print("  Fix: Create a search engine at https://programmablesearchengine.google.com/")
        print("       Copy the Search Engine ID and set GOOGLE_CSE_ID in .env")
    else:
        print("  STATUS: NOT SET (optional — DuckDuckGo + Bing used instead)")

    # Google Gemini
    print("\n--- Google Gemini API (not currently used, available for future) ---")
    if google_key:
        try:
            resp = httpx.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={google_key}",
                json={"contents": [{"parts": [{"text": "Say OK"}]}]},
                timeout=10,
            )
            if resp.status_code == 200:
                print(f"  STATUS: WORKING")
            else:
                print(f"  STATUS: ERROR ({resp.status_code}) — may be rate limited")
        except Exception as e:
            print(f"  STATUS: ERROR: {e}")
    else:
        print("  STATUS: NOT SET")

    # SMTP
    print("\n--- SMTP Accounts (email sending) ---")
    from config import SMTP_ACCOUNTS
    for i, acc in enumerate(SMTP_ACCOUNTS):
        print(f"  Account {i+1}: {acc['email']} ({'configured' if acc['password'] else 'NO PASSWORD'})")
    if len(SMTP_ACCOUNTS) < 3:
        print(f"  WARNING: Only {len(SMTP_ACCOUNTS)} account(s) configured. Add more for 1500/day capacity.")


async def test_full():
    print("\n" + "=" * 70)
    print("FULL PIPELINE TEST (cap=3 emails, dry run)")
    print("=" * 70)

    os.environ["DAILY_EMAIL_CAP"] = "3"
    os.environ["PER_ACCOUNT_CAP"] = "3"

    from run import run_pipeline
    await run_pipeline()


async def main():
    args = sys.argv[1:] if len(sys.argv) > 1 else ["all"]
    mode = args[0].lower()

    if mode in ("scrapers", "scraper"):
        await test_scrapers()
    elif mode in ("contacts", "contact"):
        await test_contacts()
    elif mode == "resume":
        test_resume()
    elif mode in ("apis", "api", "keys"):
        test_apis()
    elif mode == "full":
        await test_full()
    elif mode == "all":
        test_apis()
        await test_scrapers()
        await test_contacts()
        test_resume()
        print("\n\nAll tests complete. Run 'python test_pipeline.py full' for end-to-end test.")
    else:
        print(__doc__)


if __name__ == "__main__":
    asyncio.run(main())
