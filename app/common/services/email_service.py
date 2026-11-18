from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

import aiosmtplib

from app.core.config import settings
from app.core.logging import logger


def _from_header() -> str:
    """Sender header: SMTP_FROM (verified sender) falling back to SMTP_USER, optional display name."""
    address = settings.SMTP_FROM if isinstance(settings.SMTP_FROM, str) else ""
    address = address or settings.SMTP_USER
    name = settings.SMTP_FROM_NAME if isinstance(settings.SMTP_FROM_NAME, str) else ""
    return formataddr((name, address)) if name else str(address)


async def send_html(
    to_email: str, subject: str, html_body: str, *, cc: list[str] | None = None
) -> tuple[bool, str]:
    """Send one HTML email and report the outcome as ``(ok, error_detail)``.

    Never raises. When SMTP is not configured the message is only logged and counts as sent.
    """
    if not settings.SMTP_USER:
        logger.info(f"[Email Mock] To: {to_email} | Subject: {subject}")
        return True, ""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = _from_header()
    msg["To"] = to_email
    cc = [c for c in (cc or []) if c and c.lower() != to_email.lower()]
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg.attach(MIMEText(html_body, "html"))

    try:
        await aiosmtplib.send(
            msg,
            recipients=[to_email, *cc],
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASSWORD,
            start_tls=True,
            timeout=30,
        )
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc} (SMTP {settings.SMTP_HOST}:{settings.SMTP_PORT})"
        logger.error(f"Failed to send email to {to_email} | Subject: {subject} | {detail}")
        return False, detail
    logger.info(f"Email sent to {to_email} | Subject: {subject}")
    return True, ""


async def send_email(to_email: str, subject: str, html_body: str) -> None:
    """Send an HTML email via SMTP. Logs a mock entry when SMTP is not configured."""
    await send_html(to_email, subject, html_body)


async def send_assessment_invite(
    to_email: str, candidate_name: str, assessment_link: str, assessment_name: str
) -> None:
    """Send an assessment invitation email to a candidate."""
    html = f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:600px;margin:0 auto;padding:32px;background:#f8fafc;border-radius:12px">
      <div style="background:#2563EB;padding:20px 32px;border-radius:8px 8px 0 0">
        <h1 style="color:#fff;margin:0;font-size:22px;font-weight:700">{settings.APP_NAME}</h1>
      </div>
      <div style="background:#fff;padding:32px;border-radius:0 0 8px 8px;border:1px solid #e2e8f0">
        <p style="font-size:16px;color:#1e293b">Dear <strong>{candidate_name}</strong>,</p>
        <p style="font-size:15px;color:#475569">You have been invited to complete the <strong>{assessment_name}</strong> assessment.</p>
        <p style="font-size:15px;color:#475569">Click the button below to start your assessment:</p>
        <a href="{assessment_link}"
           style="display:inline-block;background:#2563EB;color:#fff;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:600;margin-top:8px;font-size:15px">
          Start Assessment
        </a>
        <p style="color:#94a3b8;margin-top:28px;font-size:13px">
          This link is unique to you. Do not share it with anyone.<br/>
          If you did not expect this email, please ignore it.
        </p>
      </div>
    </div>
    """
    await send_email(to_email, f"Assessment Invitation: {assessment_name}", html)
