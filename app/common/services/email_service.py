import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.core.config import settings


async def send_email(to_email: str, subject: str, html_body: str):
    if not settings.SMTP_USER:
        print(f"[Email Mock] To: {to_email} | Subject: {subject}")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_USER
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_USER, to_email, msg.as_string())
    except Exception as e:
        print(f"[Email Error] Failed to send to {to_email}: {e}")


async def send_assessment_invite(
    to_email: str, candidate_name: str, assessment_link: str, assessment_name: str
):
    html = f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:600px;margin:0 auto;padding:32px;background:#f8fafc;border-radius:12px">
      <div style="background:#2563EB;padding:20px 32px;border-radius:8px 8px 0 0">
        <h1 style="color:#fff;margin:0;font-size:22px;font-weight:700">SoftSuave Hire</h1>
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
