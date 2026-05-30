from fastapi import status


class AppException(Exception):
    def __init__(self, message: str, status_code: int = 400, detail: str = ""):
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


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
        super().__init__(message, status.HTTP_422_UNPROCESSABLE_CONTENT, detail)
