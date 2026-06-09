class SuccessMessages:
    # Auth
    LOGIN_SUCCESS = "Login successful"
    LOGOUT_SUCCESS = "Logged out successfully"
    TOKEN_REFRESHED = "Token refreshed"  # noqa: S105  # nosec B105
    SETUP_COMPLETE = "Super admin setup complete"

    # Users
    USER_CREATED = "User created successfully"
    USER_UPDATED = "User updated successfully"
    USER_DELETED = "User deleted successfully"
    USERS_RETRIEVED = "Users retrieved"
    USER_RETRIEVED = "User retrieved"

    # Candidates
    CANDIDATE_CREATED = "Candidate created successfully"
    CANDIDATE_UPDATED = "Candidate updated successfully"
    CANDIDATE_DELETED = "Candidate deactivated successfully"
    CANDIDATES_RETRIEVED = "Candidates retrieved"
    CANDIDATE_RETRIEVED = "Candidate retrieved"
    BULK_CANDIDATES_CREATED = "Bulk candidates created"
    CANDIDATES_SEARCHED = "Candidates found"

    # Workspaces
    WORKSPACE_CREATED = "Workspace created successfully"
    WORKSPACE_UPDATED = "Workspace updated successfully"
    WORKSPACE_DELETED = "Workspace deleted successfully"
    WORKSPACES_RETRIEVED = "Workspaces retrieved"
    WORKSPACE_RETRIEVED = "Workspace retrieved"
    MEMBERS_INVITED = "Members invited successfully"
    MEMBERS_RETRIEVED = "Members retrieved"
    ADMIN_USERS_RETRIEVED = "Admin users retrieved"

    # Assessments
    ASSESSMENT_CREATED = "Assessment created successfully"
    ASSESSMENT_UPDATED = "Assessment updated successfully"
    ASSESSMENT_DELETED = "Assessment deleted successfully"
    ASSESSMENTS_RETRIEVED = "Assessments retrieved"
    ASSESSMENT_RETRIEVED = "Assessment retrieved"
    SHARE_LINK_CREATED = "Share link created"
    SHARE_LINK_VALIDATED = "Share link validated"
    SHARES_RETRIEVED = "Shares retrieved"
    SHARE_REVOKED = "Share revoked"
    REACCESS_GRANTED = "Re-access granted successfully"
    SESSION_TERMINATED = "Session terminated"
    SESSION_COMPLETED = "Session completed"
    INTERVIEW_RESUMED = "Interview resumed successfully"

    # Questions
    QUESTION_CREATED = "Question created successfully"
    QUESTION_UPDATED = "Question updated successfully"
    QUESTION_DELETED = "Question deleted successfully"
    QUESTIONS_RETRIEVED = "Questions retrieved"
    QUESTION_RETRIEVED = "Question retrieved"
    CATEGORIES_RETRIEVED = "Categories retrieved"
    CATEGORY_CREATED = "Category created successfully"
    CATEGORY_UPDATED = "Category updated successfully"
    CATEGORY_DELETED = "Category and its questions deleted"
    BULK_QUESTIONS_CREATED = "Questions created in bulk"
    QUESTIONS_GENERATED = "Questions generated successfully"
    QUESTIONS_IMPORTED = "Questions imported from Excel"

    # Submissions (list)
    SUBMISSIONS_RETRIEVED = "Submissions retrieved"

    # Candidate submission flow
    ANSWER_SUBMITTED = "Answer submitted"
    ROUND_FINISHED = "Round finished"
    MALPRACTICE_RECORDED = "Activity flagged"
    MALPRACTICE_MEDIA_UPLOADED = "Malpractice media updated"
    SCREENSHOT_SAVED = "Screenshot saved"
    REACCESS_REQUESTED = "Re-access requested"
    SUBMISSION_COMPLETED = "Submission completed"
    SUBMISSION_RETRIEVED = "Submission retrieved"
    SUBMISSION_STATUS_RETRIEVED = "Submission status retrieved"
    SESSION_STATE_RETRIEVED = "Session state retrieved"
    VERSIONS_RETRIEVED = "Versions retrieved"
    ASSESSMENT_STARTED = "Assessment started"
    ASSESSMENT_PAGE_RETRIEVED = "Assessment retrieved"
    ROUND_RETRIEVED = "Round retrieved"
    LIVE_INTERVIEWS_RETRIEVED = "Live interviews retrieved"
    LIVEKIT_TOKEN_GENERATED = "LiveKit token generated"  # noqa: S105  # nosec B105

    # Notifications
    NOTIFICATION_CREATED = "Notification created"
    NOTIFICATIONS_RETRIEVED = "Notifications retrieved"
    NOTIFICATION_RETRIEVED = "Notification retrieved"
    NOTIFICATION_UPDATED = "Notification updated"
    NOTIFICATION_READ = "Notification marked as read"
    ALL_NOTIFICATIONS_READ = "All notifications marked as read"
    NOTIFICATION_DELETED = "Notification deleted"
    UNREAD_COUNT_RETRIEVED = "Unread count retrieved"

    # Export / generation
    EXPORT_READY = "Export data retrieved"
    TOKEN_GENERATED = "Token generated"  # noqa: S105  # nosec B105


class ErrorMessages:
    # Auth
    INVALID_CREDENTIALS = "Invalid credentials"
    TOKEN_EXPIRED = "Token has expired"  # noqa: S105  # nosec B105
    TOKEN_INVALID = "Invalid or missing token"  # noqa: S105  # nosec B105
    UNAUTHORIZED = "Unauthorized"
    FORBIDDEN = "Access forbidden"
    SUPER_ADMIN_EXISTS = "Super admin already exists"

    # Users / candidates
    USER_NOT_FOUND = "User not found"
    EMAIL_TAKEN = "Email already in use"
    PHONE_TAKEN = "Phone number already in use"
    INVALID_ROLE = "Invalid role specified"

    # Workspaces
    WORKSPACE_NOT_FOUND = "Workspace not found"
    WORKSPACE_ACCESS_DENIED = "You do not have access to this workspace"

    # Assessments
    ASSESSMENT_NOT_FOUND = "Assessment not found"

    # Questions
    QUESTION_NOT_FOUND = "Question not found"
    CATEGORY_NOT_FOUND = "Category not found"

    # Submissions
    SUBMISSION_NOT_FOUND = "Submission not found"
    SUBMISSION_ALREADY_COMPLETED = "Submission is already completed"
    MAX_REACCESS_REACHED = "Maximum re-access limit reached"
    INVALID_ROUND = "Invalid round number"
    MALPRACTICE_LIMIT_REACHED = "Malpractice limit reached"

    # Share links
    SHARE_LINK_INVALID = "Invalid or tampered share link"
    SHARE_LINK_EXPIRED = "This share link has expired"
    SHARE_LINK_REVOKED = "This share link has been revoked"
    SHARE_NOT_FOUND = "Share not found"
    SHARE_LINK_NOT_ACTIVE = "This interview link will become active at the scheduled time."
    SHARE_LINK_NOT_VALID = (
        "This link is not valid. Please check the URL or contact the administrator."
    )
    SHARE_LINK_REVOKED_CONTACT = "This link has been revoked. Please contact the administrator."
    SHARE_LINK_SESSION_UNAVAILABLE = (
        "This interview session is no longer available. Please contact the administrator."
    )

    # General
    VALIDATION_FAILED = "Validation failed"
    INTERNAL_ERROR = "Internal server error"
    NOT_FOUND = "Resource not found"
    CONFLICT = "Resource already exists"
    DATABASE_ERROR = "A database error occurred"
    EXTERNAL_SERVICE_ERROR = "External service unavailable"

    # Candidate session
    ALLOW_TO_INTERVIEW = "You may proceed to attend the interview."
