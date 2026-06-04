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

    if effective_monitoring.get("screenshot_enabled") and screen_image:
        key = f"evidence/{submission_id}/screen_{event_type}_{ts}.jpg"
        try:
            await s3_service.upload(screen_image, key, content_type="image/jpeg")
            result["screen_image_s3_key"] = key
        except Exception:
            logger.warning("Failed to upload screen_image for submission %s", submission_id)

    if effective_monitoring.get("video_monitoring") and face_image:
        key = f"evidence/{submission_id}/face_{event_type}_{ts}.jpg"
        try:
            await s3_service.upload(face_image, key, content_type="image/jpeg")
            result["face_image_s3_key"] = key
        except Exception:
            logger.warning("Failed to upload face_image for submission %s", submission_id)

    if effective_monitoring.get("video_monitoring") and video_chunk:
        key = f"evidence/{submission_id}/clip_{event_type}_{ts}.webm"
        try:
            await s3_service.upload(video_chunk, key, content_type="video/webm")
            result["screen_video_s3_key"] = key
        except Exception:
            logger.warning("Failed to upload video_chunk for submission %s", submission_id)

    if effective_monitoring.get("audio_monitoring") and audio_clip:
        key = f"evidence/{submission_id}/audio_{event_type}_{ts}.webm"
        try:
            await s3_service.upload(audio_clip, key, content_type="audio/webm")
            result["audio_clip_s3_key"] = key
        except Exception:
            logger.warning("Failed to upload audio_clip for submission %s", submission_id)

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
