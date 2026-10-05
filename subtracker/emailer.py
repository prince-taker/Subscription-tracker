"""The only module that talks to SMTP. Tests mock `send_email`."""

import os
import smtplib
from email.message import EmailMessage

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set. Add it to your .env file (see .env.example).")
    return value


def send_email(subject: str, body: str, html: str | None = None) -> None:
    """Send an email from GMAIL_ADDRESS to itself via Gmail SMTP over SSL.

    `body` is the plain-text version. If `html` is given the message is multipart/alternative:
    clients that can show HTML use it, the rest fall back to the plain text.
    Raises on any failure (missing config, auth, network) so callers can avoid marking
    alerts as sent. Never logs the app password.
    """
    addr = _require_env("GMAIL_ADDRESS")
    password = _require_env("GMAIL_APP_PASSWORD")
    msg = EmailMessage()
    msg["From"] = addr
    msg["To"] = addr
    msg["Subject"] = subject
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    # timeout so a dead network can't hang the scheduled job forever
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.login(addr, password)
        server.send_message(msg)
