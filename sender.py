import os
import ssl
import smtplib
import logging
from email.message import EmailMessage
from config import SMTP_ACCOUNTS, PER_ACCOUNT_CAP, PROFILE
from email_validator import validate_email_full, validate_email_list

logger = logging.getLogger(__name__)

MESSAGE_TEMPLATES = {
    "job_apply": {
        "subject": "Application for {role_title}{at_company} — {your_name}",
        "body": (
            "Dear {hr_name},\n\n"
            "I am writing to express my interest in the {role_title} position"
            "{at_company}. With 3+ years of professional experience in "
            "{top_skills}, I am confident I can contribute meaningfully "
            "from day one.\n\n"
            "What I bring to the table:\n\n"
            "  - Deep hands-on expertise in {top_skills} with production-grade deliverables\n"
            "  - Consistent track record of meeting tight deadlines and shipping quality code\n"
            "  - Strong communication skills and experience collaborating with distributed teams\n\n"
            "My resume is attached with detailed project descriptions, quantifiable achievements, "
            "and the full scope of my technical expertise.\n\n"
            "I would welcome the opportunity to discuss how my background aligns "
            "with your team's needs. I am available for a call at your convenience "
            "and can start immediately.\n\n"
            "Thank you for considering my application.\n\n"
            "Best regards,\n"
            "{your_name}\n"
            "{your_email} | {your_phone}\n"
            "{your_linkedin}"
        ),
    },
    "cold_outreach": {
        "subject": "{role_title}{at_company} — {your_name} | Available Immediately",
        "body": (
            "Dear {hr_name},\n\n"
            "I am a {role_title} with 3+ years of experience in "
            "{top_skills}. I am actively exploring new opportunities "
            "where I can make a strong impact.\n\n"
            "Key highlights:\n\n"
            "  - Production experience with {top_skills} across multiple projects\n"
            "  - Proven ability to ramp up quickly and deliver in fast-paced environments\n"
            "  - Results-driven approach with measurable outcomes in every role\n\n"
            "I have attached my resume for your review. I would appreciate "
            "a brief 10-minute conversation to explore potential fit — whether "
            "for current openings or future needs.\n\n"
            "I am open to full-time, contract, or freelance engagements and "
            "can start immediately.\n\n"
            "Thank you for your time.\n\n"
            "Best regards,\n"
            "{your_name}\n"
            "{your_email} | {your_phone}\n"
            "{your_linkedin}"
        ),
    },
    "freelance": {
        "subject": "Freelance {role_title} Available — {your_name} | Remote, Immediate Start",
        "body": (
            "Dear {hr_name},\n\n"
            "I am a freelance {role_title} with 3+ years of professional experience "
            "in {top_skills}. I am available for remote contract or freelance "
            "engagements and can start immediately.\n\n"
            "Why work with me:\n\n"
            "  - Delivered production-grade solutions using {top_skills}\n"
            "  - Flexible with time zones — experienced working with international teams\n"
            "  - Transparent communication, reliable delivery, and competitive rates\n\n"
            "I have attached my resume with detailed project descriptions and "
            "measurable achievements. I would love to discuss how I can help "
            "with your current or upcoming projects.\n\n"
            "Available for: hourly, fixed-price, or retainer-based engagements.\n\n"
            "Looking forward to connecting.\n\n"
            "Best regards,\n"
            "{your_name}\n"
            "{your_email} | {your_phone}\n"
            "{your_linkedin}"
        ),
    },
}

_account_send_counts = {}
_bounced_addresses = set()
_bounce_count_by_domain = {}
BOUNCE_DOMAIN_THRESHOLD = 2


def _reset_counts():
    global _account_send_counts, _bounced_addresses, _bounce_count_by_domain
    _account_send_counts = {acc["email"]: 0 for acc in SMTP_ACCOUNTS}
    _bounced_addresses = set()
    _bounce_count_by_domain = {}


def is_domain_bouncing(domain: str) -> bool:
    return _bounce_count_by_domain.get(domain, 0) >= BOUNCE_DOMAIN_THRESHOLD


def _get_next_account(index: int) -> dict | None:
    if not SMTP_ACCOUNTS:
        return None
    for offset in range(len(SMTP_ACCOUNTS)):
        acc = SMTP_ACCOUNTS[(index + offset) % len(SMTP_ACCOUNTS)]
        if _account_send_counts.get(acc["email"], 0) < PER_ACCOUNT_CAP:
            return acc
    return None


def compose_email(hr_name: str, company_name: str, role_title: str,
                  top_skills: str, template_key: str = "job_apply",
                  job_title: str = "") -> tuple[str, str]:
    tmpl = MESSAGE_TEMPLATES.get(template_key, MESSAGE_TEMPLATES["job_apply"])
    at_company = f" at {company_name}" if company_name.strip() else ""
    linkedin = PROFILE.get("linkedin", "")
    if linkedin and not linkedin.startswith("http"):
        linkedin = f"https://{linkedin}"
    effective_title = job_title if job_title.strip() else role_title
    replacements = {
        "hr_name": hr_name.strip() if hr_name.strip() else "Hiring Manager",
        "at_company": at_company,
        "role_title": effective_title,
        "top_skills": top_skills,
        "your_name": PROFILE.get("name", "Applicant"),
        "your_email": PROFILE.get("email", ""),
        "your_phone": PROFILE.get("phone", ""),
        "your_linkedin": linkedin,
    }
    subject = tmpl["subject"].format(**replacements)
    body = tmpl["body"].format(**replacements)
    return subject, body


def _validate_recipients(to_email: str, cc_emails: list[str] | None = None,
                         from_email: str = "") -> tuple[bool, list[str], list[str], dict]:
    """Validate all recipients. Returns (all_ok, valid_to_list, valid_cc_list, report)."""
    report = {"to": {}, "cc": {}}

    to_ok, to_reason, to_checks = validate_email_full(to_email, from_email)
    report["to"] = {"email": to_email, "valid": to_ok, "reason": to_reason, "checks": to_checks}
    if not to_ok:
        return False, [], [], report

    valid_cc = []
    if cc_emails:
        for cc in cc_emails:
            cc = cc.strip()
            if not cc:
                continue
            cc_ok, cc_reason, cc_checks = validate_email_full(cc, from_email)
            report["cc"][cc] = {"valid": cc_ok, "reason": cc_reason, "checks": cc_checks}
            if cc_ok:
                valid_cc.append(cc)
            else:
                logger.warning("CC address %s invalid: %s — skipping", cc, cc_reason)

    return True, [to_email], valid_cc, report


def send_email(to_email: str, subject: str, body: str,
               pdf_bytes: bytes, pdf_filename: str,
               smtp_account: dict, cc_emails: list[str] | None = None,
               validate: bool = True) -> dict:
    email_domain = to_email.split("@")[-1].lower()
    if is_domain_bouncing(email_domain):
        logger.info("Skipping %s — domain %s has too many bounces", to_email, email_domain)
        return {"email": to_email, "cc": cc_emails or [], "status": "skipped",
                "error": f"domain {email_domain} bounced too many times",
                "via": "skipped"}

    if validate:
        to_ok, to_reason, _ = validate_email_full(to_email, smtp_account["email"])
        if not to_ok:
            logger.warning("Validation failed for %s: %s", to_email, to_reason)
            return {"email": to_email, "cc": cc_emails or [], "status": "failed",
                    "error": f"pre-send validation: {to_reason}",
                    "via": smtp_account["email"]}

    valid_cc = []
    if cc_emails:
        for cc in cc_emails:
            cc = cc.strip()
            if not cc:
                continue
            cc_ok, cc_reason, _ = validate_email_full(cc, smtp_account["email"], skip_smtp=True)
            if cc_ok:
                valid_cc.append(cc)
            else:
                logger.warning("CC %s skipped (invalid): %s", cc, cc_reason)

    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = smtp_account["email"]
        msg["To"] = to_email
        if valid_cc:
            msg["Cc"] = ", ".join(valid_cc)
        msg.set_content(body)
        msg.add_attachment(
            pdf_bytes,
            maintype="application",
            subtype="pdf",
            filename=pdf_filename,
        )
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(smtp_account["email"], smtp_account["password"])
            all_recipients = [to_email] + valid_cc
            server.send_message(msg, to_addrs=all_recipients)

        _account_send_counts[smtp_account["email"]] = (
            _account_send_counts.get(smtp_account["email"], 0) + 1
        )
        return {"email": to_email, "cc": valid_cc, "status": "sent",
                "via": smtp_account["email"]}
    except Exception as exc:
        error_str = str(exc).lower()
        if any(phrase in error_str for phrase in (
            "does not exist", "user unknown", "no such user",
            "mailbox not found", "recipient rejected", "550 5.1.1",
        )):
            _bounced_addresses.add(to_email.lower())
            _bounce_count_by_domain[email_domain] = (
                _bounce_count_by_domain.get(email_domain, 0) + 1
            )
        logger.error("Failed to send to %s: %s", to_email, exc)
        return {"email": to_email, "cc": valid_cc, "status": "failed",
                "error": str(exc), "via": smtp_account["email"]}


def send_manual_email(to_email: str, subject: str, body: str,
                      cc_emails: list[str] | None = None,
                      attachment_bytes: bytes | None = None,
                      attachment_filename: str | None = None,
                      smtp_account: dict | None = None) -> dict:
    """Send a fully custom email with validation. For manual/ad-hoc use."""
    if not smtp_account:
        if not SMTP_ACCOUNTS:
            return {"email": to_email, "cc": cc_emails or [], "status": "failed",
                    "error": "no SMTP accounts configured"}
        smtp_account = SMTP_ACCOUNTS[0]

    from_email = smtp_account["email"]

    print(f"\n{'='*60}")
    print("  PRE-SEND VALIDATION")
    print(f"{'='*60}")

    print(f"\n  Validating TO: {to_email}")
    to_ok, to_reason, to_checks = validate_email_full(to_email, from_email)
    for check_name, check_result in to_checks.items():
        status = "PASS" if check_result["ok"] else "FAIL"
        print(f"    [{status}] {check_name}: {check_result['reason']}")

    if not to_ok:
        print(f"\n  BLOCKED: {to_reason}")
        return {"email": to_email, "cc": cc_emails or [], "status": "failed",
                "error": f"validation failed: {to_reason}"}

    valid_cc = []
    skipped_cc = []
    if cc_emails:
        for cc in cc_emails:
            cc = cc.strip()
            if not cc:
                continue
            print(f"\n  Validating CC: {cc}")
            cc_ok, cc_reason, cc_checks = validate_email_full(cc, from_email)
            for check_name, check_result in cc_checks.items():
                status = "PASS" if check_result["ok"] else "FAIL"
                print(f"    [{status}] {check_name}: {check_result['reason']}")
            if cc_ok:
                valid_cc.append(cc)
            else:
                skipped_cc.append({"email": cc, "reason": cc_reason})
                print(f"    SKIPPED: {cc_reason}")

    print(f"\n{'='*60}")
    print(f"  SENDING")
    print(f"{'='*60}")
    print(f"  From: {from_email}")
    print(f"  To:   {to_email}")
    if valid_cc:
        print(f"  CC:   {', '.join(valid_cc)}")
    if skipped_cc:
        print(f"  CC skipped: {', '.join(s['email'] for s in skipped_cc)}")
    print(f"  Subject: {subject}")
    if attachment_filename:
        print(f"  Attachment: {attachment_filename}")
    print()

    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = to_email
        if valid_cc:
            msg["Cc"] = ", ".join(valid_cc)
        msg.set_content(body)

        if attachment_bytes and attachment_filename:
            ext = attachment_filename.rsplit(".", 1)[-1].lower() if "." in attachment_filename else ""
            if ext == "pdf":
                maintype, subtype = "application", "pdf"
            elif ext in ("xlsx", "xls"):
                maintype = "application"
                subtype = "vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            elif ext in ("doc", "docx"):
                maintype = "application"
                subtype = "vnd.openxmlformats-officedocument.wordprocessingml.document"
            elif ext in ("png", "jpg", "jpeg", "gif"):
                maintype, subtype = "image", ext.replace("jpg", "jpeg")
            else:
                maintype, subtype = "application", "octet-stream"
            msg.add_attachment(
                attachment_bytes, maintype=maintype, subtype=subtype,
                filename=attachment_filename,
            )

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(from_email, smtp_account["password"])
            all_recipients = [to_email] + valid_cc
            server.send_message(msg, to_addrs=all_recipients)

        print("  SENT SUCCESSFULLY!")
        _account_send_counts[from_email] = _account_send_counts.get(from_email, 0) + 1
        return {"email": to_email, "cc": valid_cc, "skipped_cc": skipped_cc,
                "status": "sent", "via": from_email}

    except Exception as exc:
        print(f"  SEND FAILED: {exc}")
        error_str = str(exc).lower()
        if any(phrase in error_str for phrase in (
            "does not exist", "user unknown", "no such user",
            "mailbox not found", "recipient rejected", "550 5.1.1",
        )):
            _bounced_addresses.add(to_email.lower())
            email_domain = to_email.split("@")[-1].lower()
            _bounce_count_by_domain[email_domain] = (
                _bounce_count_by_domain.get(email_domain, 0) + 1
            )
        return {"email": to_email, "cc": valid_cc, "skipped_cc": skipped_cc,
                "status": "failed", "error": str(exc), "via": from_email}


def send_report_email(subject: str, body: str, attachment_bytes: bytes = None,
                      attachment_name: str = "report.xlsx"):
    if not SMTP_ACCOUNTS:
        logger.error("No SMTP accounts configured")
        return
    report_to = os.getenv("REPORT_TO_EMAIL", PROFILE.get("email", ""))
    if not report_to:
        logger.error("No REPORT_TO_EMAIL configured")
        return
    acc = SMTP_ACCOUNTS[0]
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = acc["email"]
        msg["To"] = report_to
        msg.set_content(body)
        if attachment_bytes:
            msg.add_attachment(
                attachment_bytes,
                maintype="application",
                subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=attachment_name,
            )
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(acc["email"], acc["password"])
            server.send_message(msg)
        logger.info("Report sent to %s", report_to)
    except Exception as exc:
        logger.error("Failed to send report: %s", exc)


_reset_counts()
