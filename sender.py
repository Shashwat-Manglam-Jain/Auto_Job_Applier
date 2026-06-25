import os
import ssl
import smtplib
import logging
from email.message import EmailMessage
from config import SMTP_ACCOUNTS, PER_ACCOUNT_CAP, PROFILE

logger = logging.getLogger(__name__)

MESSAGE_TEMPLATES = {
    "job_apply": {
        "subject": "Application: {role_title} | {your_name} — Available Immediately",
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
        "subject": "{role_title} — Open to Opportunities{at_company} | {your_name}",
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


def _reset_counts():
    global _account_send_counts
    _account_send_counts = {acc["email"]: 0 for acc in SMTP_ACCOUNTS}


def _get_next_account(index: int) -> dict | None:
    if not SMTP_ACCOUNTS:
        return None
    for offset in range(len(SMTP_ACCOUNTS)):
        acc = SMTP_ACCOUNTS[(index + offset) % len(SMTP_ACCOUNTS)]
        if _account_send_counts.get(acc["email"], 0) < PER_ACCOUNT_CAP:
            return acc
    return None


def compose_email(hr_name: str, company_name: str, role_title: str,
                  top_skills: str, template_key: str = "job_apply") -> tuple[str, str]:
    tmpl = MESSAGE_TEMPLATES.get(template_key, MESSAGE_TEMPLATES["job_apply"])
    at_company = f" at {company_name}" if company_name.strip() else ""
    linkedin = PROFILE.get("linkedin", "")
    if linkedin and not linkedin.startswith("http"):
        linkedin = f"https://{linkedin}"
    replacements = {
        "hr_name": hr_name.strip() if hr_name.strip() else "Hiring Manager",
        "at_company": at_company,
        "role_title": role_title,
        "top_skills": top_skills,
        "your_name": PROFILE.get("name", "Applicant"),
        "your_email": PROFILE.get("email", ""),
        "your_phone": PROFILE.get("phone", ""),
        "your_linkedin": linkedin,
    }
    subject = tmpl["subject"].format(**replacements)
    body = tmpl["body"].format(**replacements)
    return subject, body


def send_email(to_email: str, subject: str, body: str,
               pdf_bytes: bytes, pdf_filename: str,
               smtp_account: dict) -> dict:
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = smtp_account["email"]
        msg["To"] = to_email
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
            server.send_message(msg)

        _account_send_counts[smtp_account["email"]] = (
            _account_send_counts.get(smtp_account["email"], 0) + 1
        )
        return {"email": to_email, "status": "sent", "via": smtp_account["email"]}
    except Exception as exc:
        logger.error("Failed to send to %s: %s", to_email, exc)
        return {"email": to_email, "status": "failed", "error": str(exc),
                "via": smtp_account["email"]}


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
