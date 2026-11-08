"""
Email sending service.

Configure in .env / Render environment:
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USER=your-email@gmail.com
    SMTP_PASSWORD=your-app-password
    FROM_EMAIL=noreply@builderwood.com

If SMTP is not configured, emails are printed to console instead (dev mode).
"""
import smtplib
import os
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD"))


def send_email(to_email: str, subject: str, html_body: str, text_body: str = None) -> bool:
    """
    Send an email. Returns True if sent, False if not configured (dev mode logs it).
    """
    if not is_configured():
        logger.warning(
            f"[EMAIL NOT SENT — SMTP not configured]\n"
            f"To: {to_email}\n"
            f"Subject: {subject}\n"
            f"Body:\n{text_body or html_body}"
        )
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = os.getenv("FROM_EMAIL", os.getenv("SMTP_USER"))
        msg["To"] = to_email
        msg["Subject"] = subject

        if text_body:
            msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT", 587))) as server:
            server.starttls()
            server.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"))
            server.send_message(msg)

        logger.info(f"Email sent to {to_email}: {subject}")
        return True

    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False


def send_password_reset_email(to_email: str, reset_url: str) -> bool:
    """Send a password reset email."""
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #5d4037;">Reset your Builder Wood password</h2>
        <p>You (or someone else) requested a password reset for your account.</p>
        <p>Click the button below to set a new password:</p>
        <p style="margin: 30px 0;">
            <a href="{reset_url}" style="background: #5d4037; color: white; padding: 12px 24px;
               text-decoration: none; border-radius: 6px; font-weight: 700;">
                Reset Password
            </a>
        </p>
        <p style="color: #666; font-size: 13px;">
            Or copy this link into your browser:<br>
            <a href="{reset_url}">{reset_url}</a>
        </p>
        <p style="color: #999; font-size: 12px; margin-top: 30px;">
            This link expires in 1 hour. If you did not request this, ignore this email.
        </p>
    </div>
    """

    text = f"""
Reset your Builder Wood password

You requested a password reset. Open this link in your browser:
{reset_url}

This link expires in 1 hour.

If you did not request this, ignore this email.
    """

    return send_email(to_email, "Reset your Builder Wood password", html, text)