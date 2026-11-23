"""Safe upload helpers: capped reads, magic-byte sniffing, zip-bomb guard."""

import io
import zipfile

from fastapi import UploadFile

from app.common.exceptions import AppException, ValidationException

_CHUNK = 256 * 1024

MIME_JPEG = "image/jpeg"
MIME_PNG = "image/png"
MIME_GIF = "image/gif"
MIME_WEBP = "image/webp"
MIME_WEBM = "video/webm"
MIME_MP4 = "video/mp4"
MIME_OGG = "audio/ogg"
MIME_MP3 = "audio/mpeg"

# Excel (xlsx) is a zip; refuse archives that expand beyond this (zip bombs).
MAX_XLSX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_XLSX_ENTRIES = 200


class PayloadTooLargeException(AppException):
    def __init__(self, message: str = "Uploaded file is too large"):
        super().__init__(message, 413)


def sniff_mime(data: bytes) -> str | None:
    """Identify a file from its leading bytes (never from client-supplied headers)."""
    if data[:3] == b"\xff\xd8\xff":
        return MIME_JPEG
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return MIME_PNG
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return MIME_GIF
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return MIME_WEBP
    if data[:4] == b"\x1a\x45\xdf\xa3":  # EBML: WebM / Matroska
        return MIME_WEBM
    if data[4:8] == b"ftyp":
        return MIME_MP4
    if data[:4] == b"OggS":
        return MIME_OGG
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return MIME_MP3
    return None


async def read_capped(upload: UploadFile, max_bytes: int, label: str = "File") -> bytes:
    """Read an upload in chunks, aborting as soon as it exceeds ``max_bytes`` (HTTP 413)."""
    buf = bytearray()
    while True:
        chunk = await upload.read(_CHUNK)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise PayloadTooLargeException(
                f"{label} must not exceed {max_bytes // (1024 * 1024)} MB"
            )
    return bytes(buf)


async def read_validated(
    upload: UploadFile, allowed: set[str], max_bytes: int, label: str
) -> tuple[bytes, str]:
    """Capped read + magic-byte check. Returns (bytes, sniffed_mime)."""
    data = await read_capped(upload, max_bytes, label)
    if not data:
        raise ValidationException(f"{label} is empty")
    mime = sniff_mime(data)
    if mime not in allowed:
        raise ValidationException(f"{label} has an unsupported file type")
    return data, mime


def check_xlsx_archive(data: bytes) -> None:
    """Reject xlsx archives that are corrupt, have too many entries or expand too much."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_XLSX_ENTRIES:
                raise ValidationException("Spreadsheet archive has too many entries")
            if sum(i.file_size for i in infos) > MAX_XLSX_UNCOMPRESSED_BYTES:
                raise ValidationException("Spreadsheet is too large when uncompressed")
    except zipfile.BadZipFile as exc:
        raise ValidationException("File is not a valid .xlsx spreadsheet") from exc
