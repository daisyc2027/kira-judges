import re


TOPIC_RULES = [
    ("sleep", [r"\bsleep|insomnia|tired|restless|dreams?\b"]),
    ("nutrition_cravings", [r"\bcrav|appetite|hungry|chocolate|sugar|food|eat\b"]),
    ("mood", [r"\birritable|irritability|irritation|angry|sad|emotional|sensitive|mood|anxious|anxiety\b"]),
    ("pms_pmdd", [r"\bpms|pmdd|premenstrual|before my period\b"]),
    ("cycle_basics", [r"\bcycle|period|menstrual|follicular|ovulatory|luteal|hormone|estrogen|progesterone\b"]),
    ("energy", [r"\benergy|fatigue|motivation|motivated|drained\b"]),
    ("focus", [r"\bfocus|concentrat|brain fog|productive|intelligent|smart\b"]),
    ("stress", [r"\bstress|overwhelm|nervous system|panic\b"]),
    ("breathing", [r"\bbreath|breathing\b"]),
    ("mindfulness", [r"\bmindful|grounding|meditat\b"]),
    ("journaling", [r"\bjournal|write|reflection\b"]),
    ("urge_surfing", [r"\burge surf|craving wave|urge|impulse|send an angry text|angry text|snap|outburst|lash out|checking\b"]),
    ("safety", [r"\bheavy bleeding|severe pain|pmdd|suicid|self[- ]?harm|pregnan\b"]),
]

SCIENCE_TRIGGERS = [
    r"\bwhy\b",
    r"\bis it normal\b",
    r"\bis it common\b",
    r"\bis .* common\b",
    r"\bam i (less|more|better|worse)\b",
    r"\bcan .* affect\b",
    r"\bdoes .* affect\b",
    r"\bbecause i('?m| am) (on|in)\b",
    r"\bevidence|research|science|scientific\b",
    r"\bcould this be\b",
    r"\bdo i have\b",
    r"\bhormone|estrogen|progesterone|pms|pmdd|luteal|follicular|ovulatory\b",
]

PERSONAL_PATTERN_TRIGGERS = [
    r"\bdo i usually\b",
    r"\bmy pattern\b",
    r"\bfor me\b",
    r"\bmy logs\b",
    r"\bhave i been\b",
]


def _has_any(patterns, text):
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def classify_retrieval(message):
    text = (message or "").lower()
    topics = []
    for topic, patterns in TOPIC_RULES:
        if _has_any(patterns, text):
            topics.append(topic)

    personal = _has_any(PERSONAL_PATTERN_TRIGGERS, text)
    science = _has_any(SCIENCE_TRIGGERS, text)
    tool = any(topic in topics for topic in ["breathing", "mindfulness", "journaling", "urge_surfing"])

    if personal:
        intent = "personal_pattern"
        needs = True
        confidence = 0.88
    elif science and topics:
        intent = "symptom_question" if any(t in topics for t in ["mood", "sleep", "nutrition_cravings", "pms_pmdd"]) else "cycle_question"
        needs = True
        confidence = 0.84
    elif "urge_surfing" in topics:
        intent = "wellness_tool"
        needs = True
        confidence = 0.82
    elif tool and re.search(r"\b(help|try|exercise|technique|calm|cope)\b", text):
        intent = "wellness_tool"
        needs = True
        confidence = 0.78
    elif re.search(r"\b(terrible day|annoying day|lonely|can we talk|listen|upset)\b", text):
        intent = "emotional_support"
        needs = False
        confidence = 0.82
    else:
        intent = "general_chat"
        needs = False
        confidence = 0.62

    return {
        "needsScientificRetrieval": needs,
        "intent": intent,
        "topics": topics,
        "confidence": confidence,
    }
