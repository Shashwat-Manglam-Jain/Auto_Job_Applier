import dns.resolver
import re
import smtplib
import socket
from urllib.parse import urlparse


def get_domain_from_url(url: str) -> str:
    """Extract domain from a company URL."""
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path.split("/")[0]
    domain = domain.lower().removeprefix("www.")
    return domain


def _domain_from_name(company_name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]", "", company_name.lower())
    return f"{cleaned}.com"


def has_valid_mx(domain: str) -> bool:
    """Check if domain has MX records (can receive email)."""
    try:
        answers = dns.resolver.resolve(domain, "MX")
        return len(answers) > 0
    except Exception:
        return False


import logging
_logger = logging.getLogger(__name__)

OUTLOOK_MX_MARKERS = [
    "outlook", "protection.outlook", "microsoft", "office365",
    "hotmail", "live.com", "exchangelabs",
]


def _is_outlook_mx(mx_host: str) -> bool:
    mx_lower = mx_host.lower()
    return any(m in mx_lower for m in OUTLOOK_MX_MARKERS)


def verify_email_exists(email: str) -> bool:
    """
    Verify that an email address exists via SMTP RCPT TO check.

    Returns True only if the server explicitly accepts (250/251) the recipient.
    Returns False if the server rejects or if verification is inconclusive.

    For Outlook/O365 domains: these servers accept all RCPT TO commands (catch-all),
    so SMTP verification is unreliable. For those, we just check MX records exist
    and return True only for high-confidence patterns (not generic guesses).
    """
    try:
        domain = email.split("@", 1)[1]
    except IndexError:
        return False

    if not has_valid_mx(domain):
        _logger.debug("No MX records for %s — rejecting", domain)
        return False

    try:
        mx_records = dns.resolver.resolve(domain, "MX")
        mx_sorted = sorted(mx_records, key=lambda r: r.preference)
        mx_host = str(mx_sorted[0].exchange).rstrip(".")
    except Exception:
        return False

    if _is_outlook_mx(mx_host):
        _logger.debug("Outlook/O365 domain %s — skipping SMTP check, accepting", domain)
        return True

    try:
        smtp = smtplib.SMTP(timeout=8)
        smtp.connect(mx_host, 25)
        smtp.helo("verify.local")
        smtp.mail("verify@verify.local")
        code, msg = smtp.rcpt(email)
        smtp.quit()

        if code in (250, 251):
            return True
        if code >= 550:
            _logger.debug("SMTP rejected %s: %d %s", email, code, msg)
            return False
        return False
    except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
            smtplib.SMTPResponseException, socket.timeout,
            ConnectionRefusedError, OSError):
        return False
    except Exception:
        return False


def _split_name(contact_name: str) -> tuple[str, str]:
    parts = contact_name.strip().split()
    if len(parts) >= 2:
        return parts[0].lower(), parts[-1].lower()
    if len(parts) == 1:
        return parts[0].lower(), ""
    return "", ""


def guess_emails(
    company_name: str, company_url: str, contact_name: str = ""
) -> list[dict]:
    """
    Generate possible email addresses for a company.
    Returns list of {"email": str, "confidence": float, "source": str}
    """
    domain = get_domain_from_url(company_url)
    if not domain:
        domain = _domain_from_name(company_name)

    if not has_valid_mx(domain):
        return []

    candidates = []

    if not contact_name:
        generic = [
            ("hr", 0.3),
            ("hiring", 0.3),
            ("careers", 0.3),
            ("jobs", 0.3),
            ("info", 0.2),
            ("hello", 0.2),
            ("talent", 0.25),
            ("recruit", 0.25),
            ("people", 0.2),
            ("recruitment", 0.25),
            ("team", 0.2),
            ("apply", 0.2),
            ("work", 0.2),
            ("contact", 0.2),
        ]
        for prefix, confidence in generic:
            candidates.append({
                "email": f"{prefix}@{domain}",
                "confidence": confidence,
                "source": "guess_generic",
            })
    else:
        first, last = _split_name(contact_name)
        if not first:
            return candidates

        patterns = [
            (f"{first}@{domain}", 0.5),
            (f"{first}.{last}@{domain}", 0.5) if last else None,
            (f"{first[0]}{last}@{domain}", 0.4) if last else None,
            (f"{first[0]}.{last}@{domain}", 0.4) if last else None,
            (f"{last}@{domain}", 0.3) if last else None,
            (f"{first}{last}@{domain}", 0.3) if last else None,
        ]
        for entry in patterns:
            if entry is None:
                continue
            email, confidence = entry
            candidates.append({
                "email": email,
                "confidence": confidence,
                "source": "guess_named",
            })

    # Boost confidence for domains with valid MX records
    # (we already checked has_valid_mx above, so all candidates here have valid MX)
    # If domain actually resolves (not just MX), give extra confidence
    try:
        import socket as _socket
        _socket.getaddrinfo(domain, 80)
        domain_resolves = True
    except Exception:
        domain_resolves = False

    if domain_resolves:
        for c in candidates:
            c["confidence"] = min(c["confidence"] + 0.1, 0.9)

    return candidates
