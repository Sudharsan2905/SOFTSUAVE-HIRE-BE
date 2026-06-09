import re
from typing import Annotated

from pydantic import AfterValidator, Field

# ---------------------------------------------------------------------------
# Raw validation helpers — used as AfterValidator callables
# ---------------------------------------------------------------------------


def check_password_strength(v: str) -> str:
    """Raise ValueError if password lacks uppercase, lowercase, digit, or special char."""
    if not re.search(r"[A-Z]", v):
        raise ValueError("Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", v):
        raise ValueError("Password must contain at least one lowercase letter")
    if not re.search(r"\d", v):
        raise ValueError("Password must contain at least one digit")
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', v):
        raise ValueError("Password must contain at least one special character")
    return v


def validate_phone_number(v: str) -> str:
    """Accept E.164-ish numbers: optional leading +, 7–15 digits."""
    cleaned = re.sub(r"[\s\-()]", "", v)
    if not re.fullmatch(r"\+?\d{7,15}", cleaned):
        raise ValueError("Invalid phone number format (e.g. +919876543210)")
    return cleaned


def validate_object_id(v: str) -> str:
    """Ensure the value is a valid 24-hex-char MongoDB ObjectId string."""
    if not re.fullmatch(r"[0-9a-fA-F]{24}", v):
        raise ValueError("Invalid ID format")
    return v


def validate_non_empty_string(v: str) -> str:
    if not v.strip():
        raise ValueError("Field must not be blank")
    return v.strip()


# ---------------------------------------------------------------------------
# Annotated field types — attach directly to model fields for reuse
#
# Example:
#   class CreateUserRequest(BaseModel):
#       email: EmailField
#       password: PasswordField
#       phone: PhoneField
# ---------------------------------------------------------------------------

PasswordField = Annotated[
    str,
    Field(
        min_length=8,
        max_length=128,
        description="Password (min 8 chars, mixed case, digit, symbol)",
    ),
    AfterValidator(check_password_strength),
]

PhoneField = Annotated[
    str,
    Field(description="Phone number in E.164 format (e.g. +919876543210)"),
    AfterValidator(validate_phone_number),
]

ObjectIdField = Annotated[
    str,
    Field(description="MongoDB ObjectId (24 hex characters)"),
    AfterValidator(validate_object_id),
]

NonEmptyStr = Annotated[
    str,
    Field(min_length=1, description="Non-blank string"),
    AfterValidator(validate_non_empty_string),
]

# Short name field: 1–100 chars, stripped
NameField = Annotated[
    str,
    Field(min_length=1, max_length=100),
    AfterValidator(validate_non_empty_string),
]


# ---------------------------------------------------------------------------
# Reusable Pydantic field_validator factories
# Use these as classmethods inside Pydantic models when you need named fields.
#
# Example (applied to multiple fields at once):
#   @field_validator("first_name", "last_name", mode="before")
#   @classmethod
#   def strip_names(cls, v: str) -> str:
#       return strip_string(v)
# ---------------------------------------------------------------------------


def strip_string(v: str) -> str:
    return v.strip() if isinstance(v, str) else v


def lowercase_string(v: str) -> str:
    return v.strip().lower() if isinstance(v, str) else v
