"""
Multi-layer email validation fortress.

Verification chain (every email must pass ALL layers):
  1. Syntax check (RFC format)
  2. Disposable domain check (7800+ domains from GitHub list + local)
  3. Role/system prefix filter
  4. MX DNS record check
  5. MailCheck.ai API (free, no key — domain age, mx_providers, disposable, spam)
  6. Disify API (free, no key — format, dns, disposable, role)
  7. Catch-all domain detection (probe with gibberish address)
  8. SMTP RCPT TO verification (connect to MX, test if mailbox exists)

Methods documented:
  - validate_email_full()   → runs layers 1-4, 7-8
  - mailcheck_ai_verify()   → layer 5
  - disify_verify()         → layer 6
  - is_catchall_domain()    → layer 7
  - verify_smtp_rcpt()      → layer 8
"""

import re
import socket
import ssl
import smtplib
import logging
import dns.resolver
import httpx

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9](?:[a-zA-Z0-9._%+\-]*[a-zA-Z0-9])?@"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9\-]*[a-zA-Z0-9])?\."
    r"(?:[a-zA-Z]{2,}\.?)+$"
)

# --- Layer 2: Disposable domains (loaded from GitHub list + local) ---
_DISPOSABLE_DOMAINS: set[str] = set()
_disposable_loaded = False


def _load_disposable_domains():
    global _DISPOSABLE_DOMAINS, _disposable_loaded
    if _disposable_loaded:
        return
    _disposable_loaded = True

    _DISPOSABLE_DOMAINS = {
        "mailinator.com", "guerrillamail.com", "tempmail.com", "throwaway.email",
        "10minutemail.com", "trashmail.com", "yopmail.com", "sharklasers.com",
        "guerrillamailblock.com", "grr.la", "dispostable.com", "maildrop.cc",
        "temp-mail.org", "fakeinbox.com", "mailnesia.com", "tempail.com",
        "tempr.email", "discard.email", "mailcatch.com", "guerrillamail.info",
        "guerrillamail.net", "guerrillamail.org", "guerrillamail.de",
        "mailexpire.com", "throwam.com", "trashmail.net", "trashmail.me",
    }

    try:
        resp = httpx.get(
            "https://raw.githubusercontent.com/disposable-email-domains/"
            "disposable-email-domains/main/disposable_email_blocklist.conf",
            timeout=10,
        )
        if resp.status_code == 200:
            lines = resp.text.strip().split("\n")
            for line in lines:
                d = line.strip().lower()
                if d and not d.startswith("#"):
                    _DISPOSABLE_DOMAINS.add(d)
            logger.info("Loaded %d disposable domains from GitHub", len(_DISPOSABLE_DOMAINS))
    except Exception as e:
        logger.debug("Could not load disposable domains from GitHub: %s", e)


# --- Layer 3: Role/system prefixes ---
_ROLE_PREFIXES = {
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "mailer-daemon", "postmaster", "abuse", "spam",
    "unsubscribe", "bounce", "bounces", "notifications",
    "alert", "alerts", "newsletter", "test", "demo",
}

# --- Caches ---
_mx_cache: dict[str, list[str] | None] = {}
_vrfy_cache: dict[str, bool | None] = {}
_catchall_cache: dict[str, bool | None] = {}
_mailcheck_cache: dict[str, dict | None] = {}
_disify_cache: dict[str, dict | None] = {}


def validate_syntax(email: str) -> tuple[bool, str]:
    if not email or not isinstance(email, str):
        return False, "empty email"
    email = email.strip().lower()
    if len(email) > 254:
        return False, "email too long (max 254 chars)"
    if email.count("@") != 1:
        return False, "must contain exactly one @"
    local, domain = email.rsplit("@", 1)
    if not local or len(local) > 64:
        return False, "local part empty or too long"
    if not domain or len(domain) > 253:
        return False, "domain empty or too long"
    if ".." in email:
        return False, "consecutive dots not allowed"
    if not _EMAIL_RE.match(email):
        return False, "invalid email format"
    parts = domain.split(".")
    if len(parts) < 2:
        return False, "domain must have at least two parts"
    if any(len(p) == 0 for p in parts):
        return False, "domain has empty label"
    if len(parts[-1]) < 2:
        return False, "TLD too short"
    return True, "ok"


def check_disposable(email: str) -> tuple[bool, str]:
    _load_disposable_domains()
    domain = email.strip().lower().rsplit("@", 1)[-1]
    if domain in _DISPOSABLE_DOMAINS:
        return False, f"disposable domain: {domain}"
    return True, "ok"


def check_role_address(email: str) -> tuple[bool, str]:
    local = email.strip().lower().rsplit("@", 1)[0]
    for prefix in _ROLE_PREFIXES:
        if local == prefix or local.startswith(prefix + ".") or local.startswith(prefix + "+"):
            return False, f"role/system address: {local}"
    return True, "ok"


def check_mx(domain: str) -> tuple[bool, str, list[str]]:
    if domain in _mx_cache:
        records = _mx_cache[domain]
        if records:
            return True, "ok", records
        return False, f"no MX records for {domain}", []

    try:
        answers = dns.resolver.resolve(domain, "MX")
        mx_hosts = [str(r.exchange).rstrip(".") for r in sorted(answers, key=lambda r: r.preference)]
        _mx_cache[domain] = mx_hosts
        return True, "ok", mx_hosts
    except dns.resolver.NXDOMAIN:
        _mx_cache[domain] = None
        return False, f"domain {domain} does not exist (NXDOMAIN)", []
    except dns.resolver.NoAnswer:
        try:
            dns.resolver.resolve(domain, "A")
            _mx_cache[domain] = [domain]
            return True, "fallback to A record", [domain]
        except Exception:
            _mx_cache[domain] = None
            return False, f"no MX or A records for {domain}", []
    except dns.resolver.NoNameservers:
        _mx_cache[domain] = None
        return False, f"no nameservers for {domain}", []
    except Exception as e:
        _mx_cache[domain] = None
        return False, f"DNS error for {domain}: {e}", []


# --- Layer 5: MailCheck.ai API (free, no key) ---
def mailcheck_ai_verify(email: str) -> dict | None:
    domain = email.strip().lower().rsplit("@", 1)[-1]
    if domain in _mailcheck_cache:
        return _mailcheck_cache[domain]
    try:
        resp = httpx.get(
            f"https://api.mailcheck.ai/email/{email}",
            timeout=8,
        )
        if resp.status_code == 200:
            data = resp.json()
            _mailcheck_cache[domain] = data
            return data
    except Exception:
        pass
    _mailcheck_cache[domain] = None
    return None


def mailcheck_is_bad(data: dict | None) -> tuple[bool, str]:
    if not data:
        return False, "no data"
    if data.get("disposable"):
        return True, "disposable domain (mailcheck.ai)"
    if data.get("spam"):
        return True, "spam domain (mailcheck.ai)"
    if not data.get("mx"):
        return True, "no MX records (mailcheck.ai)"
    return False, "ok"


# --- Layer 6: Disify API (free, no key) ---
def disify_verify(email: str) -> dict | None:
    domain = email.strip().lower().rsplit("@", 1)[-1]
    if domain in _disify_cache:
        return _disify_cache[domain]
    try:
        resp = httpx.get(
            f"https://disify.com/api/email/{email}",
            timeout=8,
        )
        if resp.status_code == 200:
            data = resp.json()
            _disify_cache[domain] = data
            return data
    except Exception:
        pass
    _disify_cache[domain] = None
    return None


def disify_is_bad(data: dict | None) -> tuple[bool, str]:
    if not data:
        return False, "no data"
    if data.get("disposable"):
        return True, "disposable domain (disify)"
    if not data.get("dns"):
        return True, "no DNS (disify)"
    if not data.get("format"):
        return True, "bad format (disify)"
    return False, "ok"


# --- MX provider detection ---
_UNRELIABLE_MX_KEYWORDS = {
    "protection.outlook.com": "microsoft",
    "olc.protection.outlook.com": "microsoft",
    "mail.protection.outlook.com": "microsoft",
    "improvmx.com": "improvmx",
    "parkmail": "parked",
    "dynadot": "parked",
    "sedoparking": "parked",
    "pendingrenew": "parked",
    "registrar-servers": "parked",
    "above.com": "parked",
    "bodis.com": "parked",
    "mimecast.com": "mimecast",
    "pphosted.com": "proofpoint",
    "ppe-hosted.com": "proofpoint",
}

_RELIABLE_MX_KEYWORDS = {
    "aspmx.l.google.com": "google",
    "googlemail.com": "google",
    "google.com": "google",
    "smtp.secureserver.net": "godaddy",
    "zoho.com": "zoho",
}


def classify_mx_provider(mx_hosts: list[str]) -> tuple[str, bool]:
    """Classify the MX provider and whether SMTP RCPT TO is reliable.
    Returns (provider_name, is_reliable).
    Google = reliable (rejects non-existent mailboxes at SMTP level).
    Microsoft/ImprovMX/Parked/Mimecast = unreliable (accepts then bounces).
    """
    if not mx_hosts:
        return "unknown", False

    primary = mx_hosts[0].lower()

    for keyword, provider in _RELIABLE_MX_KEYWORDS.items():
        if keyword in primary:
            return provider, True

    for keyword, provider in _UNRELIABLE_MX_KEYWORDS.items():
        if keyword in primary:
            return provider, False

    return "other", False


# --- Layer 7: Catch-all detection ---
def is_catchall_domain(domain: str, mx_hosts: list[str], timeout: int = 10) -> bool:
    if domain in _catchall_cache:
        return _catchall_cache[domain]

    probe = f"zxqk7j9r3m5w_{domain.split('.')[0]}@{domain}"

    for mx_host in mx_hosts[:2]:
        try:
            with smtplib.SMTP(timeout=timeout) as smtp:
                smtp.connect(mx_host, 25)
                smtp.ehlo_or_helo_if_needed()
                try:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo_or_helo_if_needed()
                except (smtplib.SMTPNotSupportedError, smtplib.SMTPException):
                    pass
                code, _ = smtp.mail(f"test@{domain}")
                if code != 250:
                    smtp.quit()
                    continue
                code, _ = smtp.rcpt(probe)
                smtp.quit()
                is_catchall = code == 250
                _catchall_cache[domain] = is_catchall
                if is_catchall:
                    logger.debug("Domain %s is catch-all (accepts any address)", domain)
                return is_catchall
        except Exception:
            continue

    _catchall_cache[domain] = False
    return False


# --- Layer 8: SMTP RCPT TO ---
def verify_smtp_rcpt(email: str, mx_hosts: list[str], from_email: str = "",
                     timeout: int = 10) -> tuple[bool, str]:
    cache_key = email.strip().lower()
    if cache_key in _vrfy_cache:
        cached = _vrfy_cache[cache_key]
        if cached is True:
            return True, "ok (cached)"
        elif cached is False:
            return False, "rejected (cached)"

    if not from_email:
        domain = email.rsplit("@", 1)[-1]
        from_email = f"verify@{domain}"

    for mx_host in mx_hosts[:2]:
        try:
            with smtplib.SMTP(timeout=timeout) as smtp:
                smtp.connect(mx_host, 25)
                smtp.ehlo_or_helo_if_needed()

                try:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo_or_helo_if_needed()
                except (smtplib.SMTPNotSupportedError, smtplib.SMTPException):
                    pass

                code, _ = smtp.mail(from_email)
                if code != 250:
                    smtp.quit()
                    continue

                code, message = smtp.rcpt(email)
                smtp.quit()

                msg_str = message.decode("utf-8", errors="replace").lower() if isinstance(message, bytes) else str(message).lower()

                if code == 250:
                    _vrfy_cache[cache_key] = True
                    return True, "ok"
                elif code == 550:
                    if any(phrase in msg_str for phrase in (
                        "does not exist", "user unknown", "no such user",
                        "mailbox not found", "recipient rejected", "invalid",
                        "not found", "unknown user", "disabled",
                    )):
                        _vrfy_cache[cache_key] = False
                        return False, f"mailbox rejected (550): {msg_str[:100]}"
                    _vrfy_cache[cache_key] = False
                    return False, f"rejected (550): {msg_str[:100]}"
                elif code in (451, 452):
                    return False, "greylisted (temporary) — REJECTED for safety"
                elif code == 421:
                    continue
                else:
                    return False, f"inconclusive (code {code}) — REJECTED for safety"

        except socket.timeout:
            logger.debug("SMTP timeout for %s via %s", email, mx_host)
            continue
        except ConnectionRefusedError:
            logger.debug("Connection refused for %s via %s", email, mx_host)
            continue
        except OSError as e:
            logger.debug("Network error for %s via %s: %s", email, mx_host, e)
            continue
        except smtplib.SMTPException as e:
            logger.debug("SMTP error for %s via %s: %s", email, mx_host, e)
            continue

    return False, "could not verify (all MX unreachable) — REJECTED for safety"


# --- Full validation pipeline ---
def validate_email_full(email: str, from_email: str = "",
                        skip_smtp: bool = False) -> tuple[bool, str, dict]:
    email = email.strip().lower()
    checks = {}

    ok, reason = validate_syntax(email)
    checks["syntax"] = {"ok": ok, "reason": reason}
    if not ok:
        return False, f"Syntax error: {reason}", checks

    ok, reason = check_disposable(email)
    checks["disposable"] = {"ok": ok, "reason": reason}
    if not ok:
        return False, f"Disposable: {reason}", checks

    ok, reason = check_role_address(email)
    checks["role_address"] = {"ok": ok, "reason": reason}

    domain = email.rsplit("@", 1)[-1]
    ok, reason, mx_hosts = check_mx(domain)
    checks["mx"] = {"ok": ok, "reason": reason}
    if not ok:
        return False, f"MX check failed: {reason}", checks

    if not skip_smtp:
        ok, reason = verify_smtp_rcpt(email, mx_hosts, from_email)
        checks["smtp_rcpt"] = {"ok": ok, "reason": reason}
        if not ok:
            return False, f"SMTP rejected: {reason}", checks

    checks["overall"] = {"ok": True, "reason": "all checks passed"}
    return True, "valid", checks


def validate_email_list(emails: list[str], from_email: str = "",
                        skip_smtp: bool = False) -> list[dict]:
    results = []
    for email in emails:
        email = email.strip()
        if not email:
            continue
        ok, reason, checks = validate_email_full(email, from_email, skip_smtp)
        results.append({
            "email": email.lower(),
            "valid": ok,
            "reason": reason,
            "checks": checks,
        })
    return results
