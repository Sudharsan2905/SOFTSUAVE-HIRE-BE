from app.common.utils import utcnow
from app.components.storage import s3_service
from app.core.logging import logger


async def upload_evidence(
    submission_id: str,
    malpractice_type: str,
    effective_monitoring: dict,
    screen_image: bytes | None = None,
    face_image: bytes | None = None,
    video_chunk: bytes | None = None,
    audio_clip: bytes | None = None,
) -> dict:
    """Upload malpractice evidence files to S3 based on enabled monitoring features.

    Returns a dict of s3_key values (None for fields not uploaded).
    """
    ts = utcnow().strftime("%Y%m%d_%H%M%S_%f")
    event_type = malpractice_type.lower()
    result: dict[str, str | None] = {
        "screen_image_s3_key": None,
        "face_image_s3_key": None,
        "screen_video_s3_key": None,
        "audio_clip_s3_key": None,
    }

    async def _upload(
        enabled: bool,
        file_bytes: bytes | None,
        prefix: str,
        ext: str,
        content_type: str,
        result_key: str,
        label: str,
    ) -> None:
        """Upload one evidence file when enabled, recording its key in ``result``."""
        if not (enabled and file_bytes):
            return
        key = f"evidence/{submission_id}/{prefix}_{event_type}_{ts}.{ext}"
        try:
            await s3_service.upload(file_bytes, key, content_type=content_type)
            result[result_key] = key
        except Exception:
            logger.warning("Failed to upload %s for submission %s", label, submission_id)

    screenshot_on = bool(effective_monitoring.get("screenshot_enabled"))
    video_on = bool(effective_monitoring.get("video_monitoring"))
    audio_on = bool(effective_monitoring.get("audio_monitoring"))

    await _upload(
        screenshot_on,
        screen_image,
        "screen",
        "jpg",
        "image/jpeg",
        "screen_image_s3_key",
        "screen_image",
    )
    await _upload(
        video_on,
        face_image,
        "face",
        "jpg",
        "image/jpeg",
        "face_image_s3_key",
        "face_image",
    )
    await _upload(
        video_on,
        video_chunk,
        "clip",
        "webm",
        "video/webm",
        "screen_video_s3_key",
        "video_chunk",
    )
    await _upload(
        audio_on,
        audio_clip,
        "audio",
        "webm",
        "audio/webm",
        "audio_clip_s3_key",
        "audio_clip",
    )

    return result


async def resolve_evidence_urls(events: list[dict]) -> list[dict]:
    """Replace s3_key fields with pre-signed URLs in malpractice event dicts."""
    key_fields = [
        ("screen_image_s3_key", "screen_image_url"),
        ("face_image_s3_key", "face_image_url"),
        ("screen_video_s3_key", "screen_video_url"),
        ("audio_clip_s3_key", "audio_clip_url"),
    ]
    resolved = []
    for event in events:
        ev = dict(event)
        for s3_key_field, url_field in key_fields:
            key = ev.pop(s3_key_field, None)
            if key:
                try:
                    ev[url_field] = await s3_service.get_presigned_url(key)
                except Exception:
                    ev[url_field] = None
            else:
                ev[url_field] = None
        resolved.append(ev)
    return resolved


async def resolve_screenshot_urls(screenshots: list[dict]) -> list[dict]:
    """Replace s3_key with a pre-signed URL in screenshot dicts."""
    resolved = []
    for shot in screenshots:
        s = dict(shot)
        key = s.pop("s3_key", None)
        if key:
            try:
                s["url"] = await s3_service.get_presigned_url(key)
            except Exception:
                s["url"] = None
        else:
            s["url"] = None
        resolved.append(s)
    return resolved
