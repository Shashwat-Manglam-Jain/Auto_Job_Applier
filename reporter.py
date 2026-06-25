import io
import logging
from datetime import datetime, timezone, timedelta
from openpyxl import Workbook
from sender import send_report_email

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


def _build_report_text(stats: dict, sent_results: list[dict],
                       applications: list[dict]) -> str:
    now = datetime.now(IST).strftime("%B %d, %Y %I:%M %p IST")
    lines = [
        f"Daily Auto-Apply Report — {now}",
        "=" * 60,
        "",
        "SUMMARY",
        "-" * 40,
        f"  Jobs scraped (all platforms):  {stats.get('total_scraped', 0)}",
        f"  After today filter:            {stats.get('today_jobs', 0)}",
        f"  After tech + remote filter:    {stats.get('filtered_jobs', 0)}",
        f"  After dedup:                   {stats.get('unique_jobs', 0)}",
        f"  Matched to templates:          {stats.get('matched_jobs', 0)}",
        f"  Contacts discovered:           {stats.get('contacts_found', 0)}",
        f"  Emails attempted:              {stats.get('emails_attempted', 0)}",
        f"  Emails sent successfully:      {stats.get('emails_sent', 0)}",
        f"  Emails failed:                 {stats.get('emails_failed', 0)}",
        "",
    ]

    per_source = stats.get("per_source", {})
    if per_source:
        lines.append("JOBS PER PLATFORM")
        lines.append("-" * 40)
        for source, count in sorted(per_source.items(), key=lambda x: -x[1]):
            lines.append(f"  {source:30s} {count:5d}")
        lines.append("")

    per_account = stats.get("per_account", {})
    if per_account:
        lines.append("EMAILS PER SMTP ACCOUNT")
        lines.append("-" * 40)
        for account, count in per_account.items():
            lines.append(f"  {account:30s} {count:5d}")
        lines.append("")

    sent_ok = [r for r in sent_results if r.get("status") == "sent"]
    if sent_ok:
        lines.append(f"APPLICATIONS SENT ({len(sent_ok)})")
        lines.append("-" * 40)
        for i, r in enumerate(sent_ok[:50], 1):
            company = r.get("company_name", "Unknown")
            role = r.get("role_title", "Unknown")
            email = r.get("email", "Unknown")
            lines.append(f"  {i:3d}. {company} — {role} → {email}")
        if len(sent_ok) > 50:
            lines.append(f"  ... and {len(sent_ok) - 50} more (see Excel)")
        lines.append("")

    failed = [r for r in sent_results if r.get("status") == "failed"]
    if failed:
        lines.append(f"FAILED SENDS ({len(failed)})")
        lines.append("-" * 40)
        for r in failed[:20]:
            lines.append(f"  {r.get('email', '?')} — {r.get('error', 'unknown')}")
        lines.append("")

    return "\n".join(lines)


def _auto_width(ws):
    for col in ws.columns:
        max_length = 0
        for cell in col:
            if cell.value:
                max_length = max(max_length, len(str(cell.value)))
        ws.column_dimensions[col[0].column_letter].width = min(max_length + 2, 50)


def _build_excel(applications: list[dict], sent_results: list[dict],
                 all_scraped_jobs: list[dict] | None = None) -> bytes:
    wb = Workbook()

    # Sheet 1: Sent Applications
    ws = wb.active
    ws.title = "Sent Applications"
    headers = ["#", "Company", "Role", "Contact Email", "Contact Name",
               "Contact Title", "Location", "Source", "Job URL", "Status",
               "SMTP Account", "Email Template"]
    ws.append(headers)

    result_map = {r.get("email", ""): r for r in sent_results}
    sent_count = 0
    fail_count = 0
    for i, app in enumerate(applications, 1):
        result = result_map.get(app.get("contact_email", ""), {})
        status = result.get("status", "unknown")
        if status == "sent":
            sent_count += 1
        elif status == "failed":
            fail_count += 1
        ws.append([
            i,
            app.get("company_name", ""),
            app.get("title", ""),
            app.get("contact_email", ""),
            app.get("contact_name", ""),
            app.get("contact_title", ""),
            app.get("location", ""),
            app.get("source", ""),
            app.get("url", ""),
            status,
            result.get("via", ""),
            result.get("template", ""),
        ])
    _auto_width(ws)

    # Sheet 2: All Scraped Jobs (daily table)
    if all_scraped_jobs:
        ws2 = wb.create_sheet("All Scraped Jobs")
        ws2.append(["#", "Source", "Company", "Title", "Location",
                     "Tags", "Salary", "URL", "Posted"])
        for i, job in enumerate(all_scraped_jobs[:2000], 1):
            salary = ""
            if job.get("salary_min") and job.get("salary_max"):
                salary = f"${job['salary_min']:,} - ${job['salary_max']:,}"
            elif job.get("salary_min"):
                salary = f"${job['salary_min']:,}+"
            ws2.append([
                i,
                job.get("source", ""),
                job.get("company_name", ""),
                job.get("title", ""),
                job.get("location", ""),
                ", ".join(job.get("tags", [])[:5]),
                salary,
                job.get("url", ""),
                job.get("posted_at", "")[:10],
            ])
        _auto_width(ws2)

    # Sheet 3: Failed Sends
    failed = [r for r in sent_results if r.get("status") == "failed"]
    if failed:
        ws3 = wb.create_sheet("Failed Sends")
        ws3.append(["Email", "Company", "Error", "SMTP Account"])
        for r in failed:
            ws3.append([
                r.get("email", ""),
                r.get("company_name", ""),
                r.get("error", ""),
                r.get("via", ""),
            ])
        _auto_width(ws3)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def send_daily_report(stats: dict, sent_results: list[dict],
                      applications: list[dict],
                      all_scraped_jobs: list[dict] | None = None):
    today = datetime.now(IST).strftime("%Y-%m-%d")
    sent_n = stats.get("emails_sent", 0)
    fail_n = stats.get("emails_failed", 0)
    subject = f"Auto-Apply Report — {today} | {sent_n} sent, {fail_n} failed"

    body = _build_report_text(stats, sent_results, applications)
    excel_bytes = _build_excel(applications, sent_results, all_scraped_jobs)

    logger.info("Sending daily report...")
    send_report_email(
        subject=subject,
        body=body,
        attachment_bytes=excel_bytes,
        attachment_name=f"auto_apply_report_{today}.xlsx",
    )
