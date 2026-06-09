"""Tests for app.components.proctoring.proctoring_service."""

from unittest.mock import AsyncMock

import pytest

from app.components.proctoring import proctoring_service

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _patch_s3(monkeypatch):
    """Make S3 deterministic: upload echoes the key, presign returns a fixed URL."""
    monkeypatch.setattr(
        proctoring_service.s3_service, "upload", AsyncMock(side_effect=lambda b, k, **kw: k)
    )
    monkeypatch.setattr(
        proctoring_service.s3_service,
        "get_presigned_url",
        AsyncMock(return_value="https://signed.example/x"),
    )


ALL_ON = {
    "screenshot_enabled": True,
    "video_monitoring": True,
    "audio_monitoring": True,
}


async def test_upload_evidence_all_enabled():
    result = await proctoring_service.upload_evidence(
        submission_id="sub1",
        malpractice_type="TAB_SWITCH",
        effective_monitoring=ALL_ON,
        screen_image=b"img",
        face_image=b"face",
        video_chunk=b"vid",
        audio_clip=b"aud",
    )
    assert result["screen_image_s3_key"] is not None
    assert result["face_image_s3_key"] is not None
    assert result["screen_video_s3_key"] is not None
    assert result["audio_clip_s3_key"] is not None


async def test_upload_evidence_nothing_enabled():
    result = await proctoring_service.upload_evidence(
        submission_id="sub1",
        malpractice_type="TAB_SWITCH",
        effective_monitoring={},
        screen_image=b"img",
    )
    assert all(v is None for v in result.values())


async def test_upload_evidence_enabled_but_no_bytes():
    result = await proctoring_service.upload_evidence(
        submission_id="sub1",
        malpractice_type="x",
        effective_monitoring=ALL_ON,
    )
    assert all(v is None for v in result.values())


async def test_upload_evidence_handles_upload_failure(monkeypatch):
    monkeypatch.setattr(
        proctoring_service.s3_service, "upload", AsyncMock(side_effect=RuntimeError("s3 down"))
    )
    result = await proctoring_service.upload_evidence(
        submission_id="sub1",
        malpractice_type="x",
        effective_monitoring={"screenshot_enabled": True},
        screen_image=b"img",
    )
    # Failure is swallowed; key stays None
    assert result["screen_image_s3_key"] is None


async def test_resolve_evidence_urls():
    events = [
        {
            "type": "TAB_SWITCH",
            "screen_image_s3_key": "k1",
            "face_image_s3_key": None,
        }
    ]
    resolved = await proctoring_service.resolve_evidence_urls(events)
    assert resolved[0]["screen_image_url"] == "https://signed.example/x"
    assert resolved[0]["face_image_url"] is None
    assert "screen_image_s3_key" not in resolved[0]


async def test_resolve_evidence_urls_presign_error(monkeypatch):
    monkeypatch.setattr(
        proctoring_service.s3_service,
        "get_presigned_url",
        AsyncMock(side_effect=RuntimeError("fail")),
    )
    events = [{"screen_image_s3_key": "k1"}]
    resolved = await proctoring_service.resolve_evidence_urls(events)
    assert resolved[0]["screen_image_url"] is None


async def test_resolve_screenshot_urls():
    shots = [{"s3_key": "k1", "round": 1}, {"round": 2}]
    resolved = await proctoring_service.resolve_screenshot_urls(shots)
    assert resolved[0]["url"] == "https://signed.example/x"
    assert resolved[1]["url"] is None


async def test_resolve_screenshot_urls_presign_error(monkeypatch):
    monkeypatch.setattr(
        proctoring_service.s3_service,
        "get_presigned_url",
        AsyncMock(side_effect=RuntimeError("fail")),
    )
    resolved = await proctoring_service.resolve_screenshot_urls([{"s3_key": "k1"}])
    assert resolved[0]["url"] is None
