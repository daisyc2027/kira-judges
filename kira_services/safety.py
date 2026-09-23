import re


URGENT_PATTERNS = [
    r"\b(kill myself|end my life|suicidal|suicide|want to die|don't want to live)\b",
    r"\b(self[- ]?harm|hurt myself|cut myself)\b",
]

MEDICAL_PATTERNS = [
    r"\b(soaking|soaked).{0,40}\b(pad|tampon)\b",
    r"\b(very heavy|extremely heavy|heavy bleeding|unusual bleeding)\b",
    r"\b(severe|unbearable|worst).{0,40}\b(cramps|period pain|pelvic pain|pain)\b",
    r"\b(missed periods|missed my period|pregnant|pregnancy)\b",
    r"\b(fainting|passed out|dizzy and bleeding)\b",
]

SUPPORT_PATTERNS = [
    r"\b(panic attack|can't breathe|severe anxiety)\b",
    r"\b(can't function|interfering with my life|can't get out of bed)\b",
    r"\b(pmdd|do i have pmdd)\b",
    r"\b(depressed for weeks|persistent depression|severe depression)\b",
]


def _matches(patterns, text):
    reasons = []
    for pattern in patterns:
        if re.search(pattern, text, flags=re.I):
            reasons.append(pattern)
    return reasons


def evaluate_safety(message):
    text = message or ""
    urgent = _matches(URGENT_PATTERNS, text)
    if urgent:
        return {
            "level": "urgent",
            "reasons": urgent,
            "suppressCycleExplanation": True,
        }

    medical = _matches(MEDICAL_PATTERNS, text)
    if medical:
        return {
            "level": "medical_attention",
            "reasons": medical,
            "suppressCycleExplanation": True,
        }

    support = _matches(SUPPORT_PATTERNS, text)
    if support:
        return {
            "level": "support",
            "reasons": support,
            "suppressCycleExplanation": True,
        }

    return {
        "level": "none",
        "reasons": [],
        "suppressCycleExplanation": False,
    }


def urgent_response():
    return (
        "I'm really glad you told me. If you might hurt yourself or feel unsafe, "
        "please contact emergency services now or reach out to a local crisis line. "
        "If you can, tell someone nearby what is happening and stay with them while you get support."
    )

