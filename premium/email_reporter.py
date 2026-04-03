import logging
import os
import smtplib
from datetime import date
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import config


def send_report_email(
    recipient: str,
    project_name: str,
    summary: dict,
    pdf_path: str | None,
) -> bool:
    smtp_cfg = config.get_smtp_config()
    if smtp_cfg is None:
        logging.warning("email_reporter: SMTP not configured — skipping email send")
        return False

    today = date.today().strftime("%Y-%m-%d")
    subject = f"SEO Audit Report -- {project_name} -- {today}"

    pass_count = summary.get("pass_count", 0)
    fail_count = summary.get("fail_count", 0)
    needs_human = summary.get("needs_human") or []

    human_lines = "\n".join(f"  - {u}" for u in needs_human) if needs_human else "  (none)"

    pdf_available = pdf_path is not None and os.path.isfile(pdf_path)
    pdf_note = "" if pdf_available else "\nNote: PDF report unavailable.\n"

    body = (
        f"SEO Audit Report: {project_name}\n"
        f"Date: {today}\n\n"
        f"Results:\n"
        f"  Passed : {pass_count}\n"
        f"  Failed : {fail_count}\n\n"
        f"URLs flagged for human review:\n{human_lines}\n"
        f"{pdf_note}"
    )

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = smtp_cfg["from"]
    msg["To"] = recipient
    msg.attach(MIMEText(body, "plain"))

    if pdf_available:
        with open(pdf_path, "rb") as f:
            part = MIMEApplication(f.read(), _subtype="pdf")
        part.add_header(
            "Content-Disposition",
            "attachment",
            filename=os.path.basename(pdf_path),
        )
        msg.attach(part)

    try:
        with smtplib.SMTP(smtp_cfg["host"], smtp_cfg["port"]) as server:
            server.starttls()
            server.login(smtp_cfg["user"], smtp_cfg["password"])
            server.sendmail(smtp_cfg["from"], [recipient], msg.as_string())
        return True
    except Exception as exc:
        logging.error("email_reporter: failed to send email: %s", exc)
        return False
