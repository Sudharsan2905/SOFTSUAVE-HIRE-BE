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


class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"


class CandidateType(str, Enum):
    STUDENT = "student"
    PROFESSIONAL = "professional"


ADMIN_ROLES = [UserRole.SUPER_ADMIN, UserRole.ADMIN]


class MalpracticeType(str, Enum):
    TAB_SWITCH = "tab_switch"
    MULTIPLE_FACES = "multiple_faces"
    NO_FACE = "no_face"
    BACKGROUND_NOISE = "background_noise"
    COPY_PASTE = "copy_paste"
    AUDIO_VIOLATION = "audio_violation"
