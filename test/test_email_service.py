"""Tests for app/common/services/email_service.py"""

from unittest.mock import AsyncMock, patch

from app.common.services import email_service


class TestSendEmail:
    async def test_mock_path_no_smtp_user(self, capsys):
        """When SMTP_USER is empty, logs and returns without sending."""
        with patch("app.common.services.email_service.settings") as mock_settings:
            mock_settings.SMTP_USER = ""
            await email_service.send_email("to@example.com", "Subject", "<p>body</p>")

    async def test_smtp_path_success(self):
        """When SMTP_USER is set and send succeeds."""
        with patch("app.common.services.email_service.settings") as mock_settings:
            mock_settings.SMTP_USER = "sender@example.com"
            mock_settings.SMTP_HOST = "smtp.example.com"
            mock_settings.SMTP_PORT = 587
            mock_settings.SMTP_PASSWORD = "secret"
            with patch(
                "app.common.services.email_service.aiosmtplib.send", new_callable=AsyncMock
            ) as mock_send:
                await email_service.send_email("to@example.com", "Hello", "<b>hi</b>")
                mock_send.assert_called_once()

    async def test_smtp_path_failure(self):
        """When SMTP send raises, exception is caught and logged."""
        with patch("app.common.services.email_service.settings") as mock_settings:
            mock_settings.SMTP_USER = "sender@example.com"
            mock_settings.SMTP_HOST = "smtp.example.com"
            mock_settings.SMTP_PORT = 587
            mock_settings.SMTP_PASSWORD = "secret"
            with patch(
                "app.common.services.email_service.aiosmtplib.send",
                new_callable=AsyncMock,
                side_effect=ConnectionRefusedError("connection refused"),
            ):
                # Should not raise; exception is caught internally
                await email_service.send_email("to@example.com", "Fail", "<p>fail</p>")


class TestSendAssessmentInvite:
    async def test_sends_email(self):
        with patch(
            "app.common.services.email_service.send_email", new_callable=AsyncMock
        ) as mock_send:
            await email_service.send_assessment_invite(
                "candidate@example.com",
                "Alice",
                "https://app.example.com/start/abc",
                "Python Assessment",
            )
            mock_send.assert_called_once()
            args = mock_send.call_args[0]
            assert args[0] == "candidate@example.com"
            assert "Python Assessment" in args[1]
