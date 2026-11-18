"""Constants for the AI hiring pipeline."""

PIPELINES = "pipelines"
PIPELINE_CANDIDATES = "pipeline_candidates"

QUESTION_COUNTS: dict[str, int] = {"mcq_single": 10, "mcq_multi": 6, "essay": 4}
TOTAL_QUESTIONS = sum(QUESTION_COUNTS.values())
MIN_ESSAY_ANSWER_CHARS = 40
MAX_EVENTS = 300

STEP_CATALOG: list[dict[str, str]] = [
    {
        "key": "intake",
        "agent": "Profile Intake Agent",
        "title": "Read candidate profiles and resolve contacts",
        "description": "Extracts text from each resume and resolves the email and name.",
    },
    {
        "key": "analysis",
        "agent": "Candidate Analysis Agent",
        "title": "Compare every candidate with the JD",
        "description": "LLM scores skill match, gaps and interview focus topics per candidate.",
    },
    {
        "key": "questions",
        "agent": "Question Architect Agent",
        "title": "Generate the shared exam",
        "description": "One LLM call builds 10 single-answer, 6 multi-answer and 4 essay "
        "questions from the JD and the candidate pool, then validates them.",
    },
    {
        "key": "assessment",
        "agent": "Assessment Builder Agent",
        "title": "Create category, assessment and secure link",
        "description": "Saves the questions, creates one monitored assessment and an "
        "email-restricted share link.",
    },
    {
        "key": "provisioning",
        "agent": "Candidate Provisioning Agent",
        "title": "Create candidate accounts",
        "description": "Creates (or reuses) a candidate user with a temporary password.",
    },
    {
        "key": "invites",
        "agent": "Invitation Dispatch Agent",
        "title": "Email the candidates",
        "description": "Sends each candidate one email with username, temporary password and exam link.",
    },
]
STEP_KEYS = [s["key"] for s in STEP_CATALOG]
