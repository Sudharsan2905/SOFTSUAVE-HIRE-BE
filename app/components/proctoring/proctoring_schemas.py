from pydantic import BaseModel

from app.common.constants.app_constants import MalpracticeType


class MalpracticeEvidenceResult(BaseModel):
    screen_image_s3_key: str | None = None
    face_image_s3_key: str | None = None
    screen_video_s3_key: str | None = None
    audio_clip_s3_key: str | None = None


class ProctoringEvent(BaseModel):
    submission_id: str
    malpractice_type: MalpracticeType
    round: int
    screen_image_bytes: bytes | None = None
    face_image_bytes: bytes | None = None
    video_chunk_bytes: bytes | None = None
    audio_clip_bytes: bytes | None = None
