# Auto Job Applier — System Flow

## Overview

Automated daily pipeline that scrapes remote tech jobs, finds company contacts, generates customized resumes, and sends applications via email. Runs as a GitHub Actions cron job at 6 AM IST.

## Architecture

```
auto-job-applier/
├── run.py                    # Main pipeline orchestrator
├── config.py                 # All settings from .env
├── test_pipeline.py          # Test script (scrapers/contacts/resume/apis/full)
│
├── scrapers/                 # Job scraping (38 scrapers across 3 types)
│   ├── base.py               # BaseScraper — rate limiting, retry, date filter
│   ├── api_scrapers.py       # 15 API scrapers (JSON endpoints)
│   ├── rss_scrapers.py       # 8 RSS feed scrapers
│   └── html_scrapers.py      # 15 HTML page scrapers
│
├── contacts/                 # Contact discovery
│   ├── finder.py             # Orchestrator — runs all methods in parallel
│   ├── search_engine.py      # DuckDuckGo + Bing + Google API search
│   ├── website_scraper.py    # Company /about /team /contact pages
│   └── email_guesser.py      # Pattern generation + MX validation
│
├── matcher.py                # Job → resume template matching (keyword + Groq AI)
├── customizer.py             # Dynamic resume generation (Groq AI)
├── resume_templates.py       # 21 role templates + PDF generation (fpdf2)
├── scorer.py                 # Company priority scoring (0.0-1.0)
├── sender.py                 # SMTP email sending (3-account rotation)
├── reporter.py               # Daily summary report + Excel attachment
│
└── .github/workflows/
    └── daily_apply.yml       # GitHub Actions cron (6 AM IST daily)
```

## Pipeline Flow (run.py)

```
Step 1: SCRAPE
    ↓  38 scrapers fetch jobs from remote job platforms
    ↓  Each scraper: API call/RSS parse/HTML scrape → list of job dicts
    ↓  Result: ~100-500 raw jobs

Step 2: FILTER
    ↓  Keep only: tech jobs + India-eligible (no US-only, no visa-required)
    ↓  Filter by keywords in title/tags/description
    ↓  Result: ~50-200 filtered jobs

Step 3: DEDUP
    ↓  Remove duplicates by (company_name, title) across platforms
    ↓  Same job on RemoteOK + Remotive → keep one
    ↓  Result: ~40-150 unique jobs

Step 4: MATCH
    ↓  Map each job to one of 21 resume templates
    ↓  Hybrid matching: keyword scoring first, Groq AI fallback
    ↓  Confidence threshold: ≥0.3 (configurable)
    ↓  Result: ~30-120 matched jobs

Step 5: SCORE & SORT
    ↓  Score companies 0.0-1.0 (startup? remote-first? salary listed?)
    ↓  Sort highest-score first → best companies get emails first
    ↓  Result: prioritized job list

Step 6: FIND CONTACTS
    ↓  For each company (parallel):
    ↓    → Search engines (DDG + Bing) for HR/CTO/CEO emails
    ↓    → Scrape company website (/about, /team, /contact)
    ↓    → Generate email patterns (hr@, hiring@, firstname@)
    ↓    → Validate with MX record check
    ↓  Dedup by email, sort by confidence
    ↓  Cap at DAILY_EMAIL_CAP (default 1500)
    ↓  Result: list of {job + contact_email + contact_name}

Step 7: SEND EMAILS
    ↓  For each application:
    ↓    → Generate custom resume PDF (Groq AI for summary + projects)
    ↓    → Compose email (job_apply or cold_outreach template)
    ↓    → Send via SMTP (round-robin across 3 Gmail accounts)
    ↓    → Random 3-10s delay between sends
    ↓  Track: sent count, failures, per-account breakdown

Step 8: REPORT
    → Email summary to REPORT_TO_EMAIL
    → Includes: stats, per-platform breakdown, applications list
    → Attached: Excel file with all application details
```

## API Keys & Services

| Service | Key | Purpose | Status Check |
|---------|-----|---------|--------------|
| **Groq** | `GROQ_API_KEY` | AI resume customization, smart role matching | `python test_pipeline.py apis` |
| **Google Search** | `GOOGLE_API_KEY` + `GOOGLE_CSE_ID` | Contact discovery (optional, 100/day free) | Needs CSE setup |
| **Google Gemini** | `GOOGLE_API_KEY` | Not used currently (available for future) | — |
| **Gmail SMTP** | `SMTP_ACCOUNT_1/2/3` + `SMTP_PASSWORD_1/2/3` | Email sending (500/account/day) | — |

### API Usage Per Run

- **Groq**: ~2 calls per job (summary + projects). 100 jobs = ~200 calls. Free tier: 30 req/min, 14.4K req/day. Well within limits.
- **Google Search**: 2 calls per company (if configured). Optional — DDG+Bing used as primary.
- **DuckDuckGo**: 2 queries per company. No API key needed. 2000+/day easily.
- **Bing**: 2 queries per company. No API key needed. 500+/day with delays.
- **Gmail SMTP**: 500/account/day. 3 accounts = 1500/day max.

## Resume Customization (customizer.py)

Each resume is unique per job:

1. **Skill Reordering** — Job-relevant skills moved to top of each category
2. **Project Reordering** — Most relevant existing projects sorted first
3. **AI Summary** (Groq) — Professional summary rewritten to target specific job
4. **AI Projects** (Groq) — 3 new projects generated matching job's tech stack with measurable results
5. **Fallback** — If Groq unavailable, uses keyword reordering only (still customized, just not AI-enhanced)

## Role Matching (matcher.py)

21 resume templates available:

```
backend_engineer, frontend_engineer, fullstack_developer, devops_engineer,
data_engineer, ml_engineer, data_analyst_bi, mobile_app_developer,
software_tester_qa, cloud_architect, cybersecurity_analyst, embedded_developer,
blockchain_developer, game_developer, site_reliability_engineer, technical_writer,
software_engineer, product_manager, ux_designer, database_administrator,
network_engineer
```

Matching process:
1. **Keyword scoring**: +5 title match, +2 tag match, +1 description match
2. **Confidence**: score / 15 (normalized to 0.0-1.0)
3. **Groq fallback**: If confidence < 0.5, ask AI to classify the role
4. **Threshold**: Jobs below MIN_MATCH_CONFIDENCE (0.3) are skipped

## Contact Discovery (contacts/)

Three methods run in parallel:

1. **Search Engine** (`search_engine.py`)
   - 4 queries per company: HR email, CTO/CEO email, @domain, LinkedIn
   - Alternates DuckDuckGo and Bing (Google API for first 2 if configured)
   - Extracts emails + names from search results
   - Filters garbage names (common words, browser metadata)

2. **Website Scraper** (`website_scraper.py`)
   - Fetches /about, /team, /people, /contact pages
   - Extracts emails from mailto links and page text
   - Parses JSON-LD structured data

3. **Email Guesser** (`email_guesser.py`)
   - Generic patterns: hr@, hiring@, careers@, jobs@, info@, hello@
   - Name-based patterns: firstname@, firstname.lastname@, f.lastname@
   - MX record validation before generating

## Company Scoring (scorer.py)

```
+0.3  Startup indicators (seed, series A, YC, small team)
+0.2  Posted today (freshest jobs)
+0.2  Remote-first company
+0.1  Salary listed (transparent = serious)
+0.1  Tech-related tags
+0.1  Has company URL
= 1.0 max
```

## Email Sending (sender.py)

- 3 Gmail accounts, round-robin rotation
- 500 emails/account/day limit
- SSL connection (port 465)
- App passwords required (not regular Gmail passwords)
- Random 3-10s delay between sends to avoid spam flags
- Failed sends don't count against daily limit

## Testing

```bash
# Test individual components
python test_pipeline.py scrapers    # Test all 38 scrapers
python test_pipeline.py contacts    # Test contact discovery
python test_pipeline.py resume      # Test dynamic resume generation
python test_pipeline.py apis        # Verify API keys are working

# Full pipeline test (sends max 3 emails)
python test_pipeline.py full

# Run all tests
python test_pipeline.py all
```

## GitHub Actions Setup

1. Create repo, push code
2. Add secrets in Settings → Secrets → Actions:
   - All .env variables as repository secrets
3. Workflow runs daily at 6:00 AM IST (00:30 UTC)
4. Manual trigger available via "Run workflow" button
5. Free for public repos, 2000 min/month for private

## Environment Variables (.env)

See `.env.example` for all required variables. Key ones:

- `GROQ_API_KEY` — Get from https://console.groq.com (free)
- `GOOGLE_API_KEY` — Get from Google Cloud Console (optional)
- `SMTP_ACCOUNT_1/2` — Gmail addresses
- `SMTP_PASSWORD_1/2` — Gmail App Passwords (Settings → Security → App Passwords)
- `DAILY_EMAIL_CAP` — Max emails/day (default 1500)
- `REPORT_TO_EMAIL` — Where to send daily summary
