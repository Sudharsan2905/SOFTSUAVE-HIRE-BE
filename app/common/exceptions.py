from fastapi import status


class AppException(Exception):
    """Base exception for all application-level errors."""

    def __init__(self, message: str, status_code: int = 400, detail: str = ""):
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


# ---------------------------------------------------------------------------
# HTTP-mapped exceptions
# ---------------------------------------------------------------------------


class UnauthorizedException(AppException):
    def __init__(self, message: str = "Unauthorized", detail: str = ""):
        super().__init__(message, status.HTTP_401_UNAUTHORIZED, detail)


class ForbiddenException(AppException):
    def __init__(self, message: str = "Access forbidden", detail: str = ""):
        super().__init__(message, status.HTTP_403_FORBIDDEN, detail)


class NotFoundException(AppException):
    def __init__(self, message: str = "Resource not found", detail: str = ""):
        super().__init__(message, status.HTTP_404_NOT_FOUND, detail)


class ConflictException(AppException):
    def __init__(self, message: str = "Resource already exists", detail: str = ""):
        super().__init__(message, status.HTTP_409_CONFLICT, detail)


class ValidationException(AppException):
    def __init__(self, message: str = "Validation failed", detail: str = ""):
        super().__init__(message, status.HTTP_422_UNPROCESSABLE_ENTITY, detail)


# ---------------------------------------------------------------------------
# Domain-level exceptions
# ---------------------------------------------------------------------------


class BusinessRuleException(AppException):
    """Raised when an operation violates a domain business rule.

    Example: exceeding the maximum re-access count, submitting to a
    terminated session, or attempting an invalid state transition.
    """

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message, status.HTTP_422_UNPROCESSABLE_ENTITY, detail)


class DatabaseException(AppException):
    """Raised when a database operation fails unexpectedly.

    Wraps pymongo / motor errors so the service layer never leaks
    driver-specific error types to callers.
    """

    def __init__(self, message: str = "A database error occurred", detail: str = ""):
        super().__init__(message, status.HTTP_500_INTERNAL_SERVER_ERROR, detail)


class ExternalServiceException(AppException):
    """Raised when a third-party service (OpenAI, S3, LiveKit, SMTP, etc.)
    returns an unexpected error or is temporarily unavailable.
    """

    def __init__(self, message: str = "External service unavailable", detail: str = ""):
        super().__init__(message, status.HTTP_502_BAD_GATEWAY, detail)


class RateLimitException(AppException):
    """Raised when an internal rate or quota limit is exceeded at the
    business-logic layer (distinct from the HTTP 429 returned by slowapi).
    """

    def __init__(self, message: str = "Rate limit exceeded", detail: str = ""):
        super().__init__(message, status.HTTP_429_TOO_MANY_REQUESTS, detail)
