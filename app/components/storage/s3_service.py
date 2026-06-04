"""
AWS S3 service using aioboto3 (async boto3).

Provides upload, pre-signed URL generation, and delete operations.
All functions are async and share a module-level aioboto3.Session singleton
for connection reuse.
"""

import aioboto3

from app.core.config import settings

# ---------------------------------------------------------------------------
# Module-level session singleton
# ---------------------------------------------------------------------------
_session = aioboto3.Session(
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION,
)

# ---------------------------------------------------------------------------
# S3 key prefix constants
# ---------------------------------------------------------------------------
S3_PREFIX_SCREENSHOTS = "screenshots"
S3_PREFIX_EVIDENCE = "evidence"


# ---------------------------------------------------------------------------
# S3 key naming helpers
# ---------------------------------------------------------------------------


def make_screenshot_key(
    submission_id: str,
    round_n: int,
    ts: str,
    ext: str = ".jpg",
) -> str:
    """
    Build an S3 key for a periodic screenshot.

    Pattern: screenshots/<submission_id>/round_<round_n>/<ts><ext>

    Args:
        submission_id: Unique identifier for the candidate submission.
        round_n:       Interview round number.
        ts:            Timestamp string (e.g. ISO-8601 or Unix epoch).
        ext:           File extension including the leading dot (default ".jpg").

    Returns:
        A slash-delimited S3 object key string.
    """
    return f"{S3_PREFIX_SCREENSHOTS}/{submission_id}/round_{round_n}/{ts}{ext}"


def make_evidence_key(
    submission_id: str,
    round_n: int,
    malpractice_type: str,
    media_type: str,
    ts: str,
    ext: str = "",
) -> str:
    """
    Build an S3 key for a malpractice evidence artifact.

    Pattern: evidence/<submission_id>/round_<round_n>/<malpractice_type>/<media_type>_<ts><ext>

    Args:
        submission_id:     Unique identifier for the candidate submission.
        round_n:           Interview round number.
        malpractice_type:  Category of malpractice (e.g. "tab_switch", "face_absent").
        media_type:        Artifact kind — one of "screen", "face", "clip", "audio".
        ts:                Timestamp string (e.g. ISO-8601 or Unix epoch).
        ext:               File extension including the leading dot (e.g. ".mp4").

    Returns:
        A slash-delimited S3 object key string.
    """
    return (
        f"{S3_PREFIX_EVIDENCE}/{submission_id}/round_{round_n}"
        f"/{malpractice_type}/{media_type}_{ts}{ext}"
    )


# ---------------------------------------------------------------------------
# Core S3 operations
# ---------------------------------------------------------------------------


async def upload(
    file_bytes: bytes,
    s3_key: str,
    content_type: str = "image/jpeg",
) -> str:
    """
    Upload raw bytes to S3 and return the object key.

    Args:
        file_bytes:   Binary content to upload.
        s3_key:       Destination key inside the configured S3 bucket.
        content_type: MIME type for the stored object (default "image/jpeg").

    Returns:
        The s3_key that was used for the upload.
    """
    async with _session.client("s3") as s3:
        await s3.put_object(
            Bucket=settings.S3_BUCKET_NAME,
            Key=s3_key,
            Body=file_bytes,
            ContentType=content_type,
        )
    return s3_key


async def get_presigned_url(s3_key: str, expires_in: int = 3600) -> str:
    """
    Generate a pre-signed GET URL for an S3 object.

    Args:
        s3_key:     Key of the object inside the configured S3 bucket.
        expires_in: URL validity duration in seconds (default 3600 = 1 hour).

    Returns:
        A pre-signed HTTPS URL string that allows unauthenticated GET access
        for the specified duration.
    """
    async with _session.client("s3") as s3:
        url: str = await s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": settings.S3_BUCKET_NAME,
                "Key": s3_key,
            },
            ExpiresIn=expires_in,
        )
    return url


async def delete(s3_key: str) -> None:
    """
    Delete an object from S3.

    Args:
        s3_key: Key of the object to remove from the configured S3 bucket.
    """
    async with _session.client("s3") as s3:
        await s3.delete_object(
            Bucket=settings.S3_BUCKET_NAME,
            Key=s3_key,
        )
