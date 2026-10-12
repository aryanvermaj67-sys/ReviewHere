"""Outgoing email, used only for password resets.

Deliberately plain SMTP: every provider speaks it, so switching vendors is an
environment-variable change rather than a code change. When SMTP is not configured
the message is printed to the server log - so a locked-out owner can still be
rescued with `fly logs` instead of the reset being silently lost.
"""
import smtplib
import ssl
from email.message import EmailMessage

from . import config


def send(to: str, subject: str, body: str) -> bool:
    """Returns True if the message was handed to a mail server."""
    if not config.MAIL_CONFIGURED:
        print("[mail] SMTP not configured - message for %s not sent" % to)
        print("[mail] subject: %s" % subject)
        print("[mail] body:\n%s" % body)
        return False

    message = EmailMessage()
    message["From"] = config.MAIL_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        if config.SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(
                config.SMTP_HOST, config.SMTP_PORT, timeout=15,
                context=ssl.create_default_context(),
            )
        else:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15)
        with server:
            if config.SMTP_PORT != 465:
                server.starttls(context=ssl.create_default_context())
            if config.SMTP_USER:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(message)
        return True
    except Exception as exc:  # noqa: BLE001 - a failed reset must not 500 the request
        print("[mail] delivery to %s failed: %s" % (to, exc))
        return False
