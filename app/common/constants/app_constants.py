from enum import Enum


class UserRole(str, Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    CANDIDATE = "candidate"


class QuestionType(str, Enum):
    MCQ_SINGLE = "mcq_single"
    MCQ_MULTI = "mcq_multi"
    ESSAY = "essay"


class Complexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AssessmentAccessibility(str, Enum):
    NORMAL = "normal"
    MONITORING = "monitoring"


class SubmissionStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ON_HOLD = "on_hold"  # network loss pause — awaiting admin resume
    TERMINATED = "terminated"  # admin-forced termination
    MALPRACTICE = "malpractice"


class SubmissionResult(str, Enum):
    """Admin-facing outcome of a finished submission, derived from the assessment pass mark."""

    PASSED = "passed"
    FAILED = "failed"


# Pass mark (overall percentage) applied when an assessment has none configured.
DEFAULT_PASS_PERCENTAGE = 50


class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"


class CandidateType(str, Enum):
    STUDENT = "student"
    PROFESSIONAL = "professional"


ADMIN_ROLES = [UserRole.SUPER_ADMIN, UserRole.ADMIN]

MAX_MALPRACTICE_COUNT = 3


class MalpracticeType(str, Enum):
    TAB_SWITCH = "tab_switch"
    MULTIPLE_FACES = "multiple_faces"
    FACE_ABSENCE = "face_absence"
    BACKGROUND_NOISE = "background_noise"
    COPY_PASTE = "copy_paste"
    AUDIO_VIOLATION = "audio_violation"
    FULLSCREEN_EXIT = "fullscreen_exit"
    SCREEN_SHARE_STOP = "screen_share_stop"
    DEVTOOLS_OPEN = "devtools_open"
    KEYBOARD_SHORTCUT = "keyboard_shortcut"
    EYE_DIRECTION = "eye_direction"
    SPEAKING = "speaking"
    NOTIFICATION_RECEIVED = "notification_received"
    MULTIPLE_MONITORS = "multiple_monitors"


class ReaccessReasonCategory(str, Enum):
    POOR_NETWORK = "poor_network"
    CANDIDATE_REQUEST = "candidate_request"
    TECHNICAL_ISSUE = "technical_issue"
    OTHER = "other"


class RestrictionMode(str, Enum):
    INCLUDE = "INCLUDE"
    EXCLUDE = "EXCLUDE"
