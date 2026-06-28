import os
import json
import logging
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

_conn = None


def _get_conn():
    global _conn
    url = os.getenv("NEON_DATABASE_URL", "")
    if not url:
        raise RuntimeError("NEON_DATABASE_URL not set")
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(url)
        _conn.autocommit = True
    return _conn


def init_db():
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            domain VARCHAR(255),
            url VARCHAR(500),
            created_at TIMESTAMP DEFAULT NOW(),
            CONSTRAINT companies_name_key UNIQUE (name)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id SERIAL PRIMARY KEY,
            company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
            source VARCHAR(50) NOT NULL,
            source_id VARCHAR(255),
            title VARCHAR(500) NOT NULL,
            url VARCHAR(1000),
            description TEXT,
            tags TEXT,
            location VARCHAR(255),
            salary_min INTEGER,
            salary_max INTEGER,
            role_key VARCHAR(50),
            match_confidence FLOAT,
            posted_at VARCHAR(100),
            scraped_at TIMESTAMP DEFAULT NOW(),
            CONSTRAINT jobs_source_source_id_key UNIQUE (source, source_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id SERIAL PRIMARY KEY,
            company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
            email VARCHAR(255) NOT NULL,
            name VARCHAR(255) DEFAULT '',
            title VARCHAR(255) DEFAULT '',
            confidence FLOAT DEFAULT 0,
            source VARCHAR(100) DEFAULT '',
            verified BOOLEAN DEFAULT FALSE,
            discovered_at TIMESTAMP DEFAULT NOW(),
            CONSTRAINT contacts_email_company_id_key UNIQUE (email, company_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id SERIAL PRIMARY KEY,
            job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
            contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
            status VARCHAR(50) DEFAULT 'pending',
            sent_at TIMESTAMP,
            sent_via VARCHAR(255),
            error TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sent_companies (
            id SERIAL PRIMARY KEY,
            company_name VARCHAR(255) NOT NULL,
            email_used VARCHAR(255) NOT NULL,
            sent_at TIMESTAMP DEFAULT NOW(),
            sent_via VARCHAR(255) DEFAULT '',
            month_key VARCHAR(7) DEFAULT TO_CHAR(NOW(), 'YYYY-MM'),
            CONSTRAINT sent_companies_name_email_key UNIQUE (company_name, email_used)
        )
    """)
    cur.close()
    logger.info("Neon DB tables initialized")


def save_company(name: str, domain: str = "", url: str = "") -> int | None:
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO companies (name, domain, url)
            VALUES (%s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET
                domain = COALESCE(NULLIF(EXCLUDED.domain, ''), companies.domain),
                url = COALESCE(NULLIF(EXCLUDED.url, ''), companies.url)
            RETURNING id
        """, (name.strip(), domain, url))
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None
    except Exception as e:
        logger.debug("save_company failed for %s: %s", name, e)
        return None


def save_job(company_id: int, source: str, source_id: str, title: str,
             url: str = "", description: str = "", tags: list = None,
             location: str = "", salary_min: int = None, salary_max: int = None,
             role_key: str = "", match_confidence: float = 0,
             posted_at: str = "") -> int | None:
    try:
        conn = _get_conn()
        cur = conn.cursor()
        tags_json = json.dumps(tags or [])
        cur.execute("""
            INSERT INTO jobs (company_id, source, source_id, title, url, description,
                              tags, location, salary_min, salary_max, role_key,
                              match_confidence, posted_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source, source_id) DO UPDATE SET
                title = EXCLUDED.title,
                url = EXCLUDED.url,
                description = EXCLUDED.description,
                tags = EXCLUDED.tags,
                location = EXCLUDED.location,
                salary_min = EXCLUDED.salary_min,
                salary_max = EXCLUDED.salary_max,
                role_key = EXCLUDED.role_key,
                match_confidence = EXCLUDED.match_confidence,
                scraped_at = NOW()
            RETURNING id
        """, (company_id, source, source_id, title, url,
              (description or "")[:5000], tags_json, location,
              salary_min, salary_max, role_key, match_confidence, posted_at))
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None
    except Exception as e:
        logger.debug("save_job failed for %s/%s: %s", source, source_id, e)
        return None


def save_contact(company_id: int, email: str, name: str = "", title: str = "",
                 confidence: float = 0, source: str = "",
                 verified: bool = False) -> int | None:
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO contacts (company_id, email, name, title, confidence, source, verified)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (email, company_id) DO UPDATE SET
                confidence = GREATEST(contacts.confidence, EXCLUDED.confidence),
                name = COALESCE(NULLIF(EXCLUDED.name, ''), contacts.name),
                title = COALESCE(NULLIF(EXCLUDED.title, ''), contacts.title),
                verified = EXCLUDED.verified OR contacts.verified
            RETURNING id
        """, (company_id, email.lower().strip(), name, title, confidence, source, verified))
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None
    except Exception as e:
        logger.debug("save_contact failed for %s: %s", email, e)
        return None


def save_application(job_id: int, contact_id: int | None, status: str = "pending",
                     sent_at: str = None, sent_via: str = "",
                     error: str = "") -> int | None:
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO applications (job_id, contact_id, status, sent_at, sent_via, error)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (job_id, contact_id, status, sent_at, sent_via, error))
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None
    except Exception as e:
        logger.debug("save_application failed: %s", e)
        return None


def update_application_status(application_id: int, status: str,
                              sent_via: str = "", error: str = ""):
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            UPDATE applications
            SET status = %s, sent_at = NOW(), sent_via = %s, error = %s
            WHERE id = %s
        """, (status, sent_via, error, application_id))
        cur.close()
    except Exception as e:
        logger.debug("update_application_status failed: %s", e)


def mark_company_sent(company_name: str, email_used: str, sent_via: str = ""):
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO sent_companies (company_name, email_used, sent_via)
            VALUES (LOWER(TRIM(%s)), LOWER(TRIM(%s)), %s)
            ON CONFLICT (company_name, email_used) DO UPDATE SET
                sent_at = NOW(), sent_via = EXCLUDED.sent_via
        """, (company_name, email_used, sent_via))
        cur.close()
    except Exception as e:
        logger.debug("mark_company_sent failed: %s", e)


def get_sent_company_names() -> set:
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT company_name FROM sent_companies")
        names = {row[0] for row in cur.fetchall()}
        cur.close()
        return names
    except Exception as e:
        logger.debug("get_sent_company_names failed: %s", e)
        return set()


def clear_monthly_scraped():
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM applications")
        cur.execute("DELETE FROM contacts")
        cur.execute("DELETE FROM jobs")
        cur.execute("DELETE FROM companies")
        cur.close()
        logger.info("Cleared monthly scraped data (kept sent_companies)")
    except Exception as e:
        logger.debug("clear_monthly_scraped failed: %s", e)
