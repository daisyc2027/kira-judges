from datetime import datetime


DEFAULT_SESSION_ID = "default"
RECENT_LIMIT = 8
SUMMARY_THRESHOLD = 16
MAX_FACT_CACHE = 24


def session_ref(db, user_id, session_id=DEFAULT_SESSION_ID):
    return (
        db.collection("users")
        .document(user_id)
        .collection("chat_sessions")
        .document(session_id or DEFAULT_SESSION_ID)
    )


def load_session(db, user_id, session_id=DEFAULT_SESSION_ID):
    doc = session_ref(db, user_id, session_id).get()
    data = doc.to_dict() if doc.exists else {}
    return {
        "sessionId": session_id or DEFAULT_SESSION_ID,
        "summary": data.get("summary", ""),
        "retrievedFactIds": data.get("retrievedFactIds", []),
        "activeTopics": data.get("activeTopics", []),
        "lastRetrievalAt": data.get("lastRetrievalAt"),
        "messageCount": data.get("messageCount", 0),
    }


def add_fact_ids(db, user_id, session_id, fact_ids, topics=None):
    if not fact_ids:
        return
    session = load_session(db, user_id, session_id)
    merged = []
    for fact_id in session.get("retrievedFactIds", []) + list(fact_ids):
        if fact_id not in merged:
            merged.append(fact_id)
    merged = merged[-MAX_FACT_CACHE:]
    session_ref(db, user_id, session_id).set({
        "retrievedFactIds": merged,
        "activeTopics": list(dict.fromkeys((session.get("activeTopics") or []) + (topics or []))),
        "lastRetrievalAt": datetime.utcnow().isoformat(),
    }, merge=True)


def get_recent_messages(db, user_id, limit=RECENT_LIMIT):
    docs = (
        db.collection("users")
        .document(user_id)
        .collection("chat_history")
        .order_by("timestamp")
        .limit_to_last(limit)
        .get()
    )
    return [{"role": doc.to_dict().get("role"), "content": doc.to_dict().get("content", "")} for doc in docs]


def make_session_summary(existing_summary, recent_messages):
    user_reports = [
        msg["content"].strip()
        for msg in recent_messages
        if msg.get("role") == "user" and msg.get("content")
    ][-6:]
    if not user_reports:
        return existing_summary or ""
    joined = " | ".join(user_reports)
    prefix = "User-reported recent themes: "
    summary = (existing_summary + " " + prefix + joined).strip() if existing_summary else prefix + joined
    return summary[:900]


def update_summary_if_needed(db, user_id, session_id, recent_messages):
    session = load_session(db, user_id, session_id)
    count = int(session.get("messageCount") or 0) + 2
    payload = {"messageCount": count, "updatedAt": datetime.utcnow().isoformat()}
    if count >= SUMMARY_THRESHOLD and count % 8 == 0:
        payload["summary"] = make_session_summary(session.get("summary", ""), recent_messages)
    session_ref(db, user_id, session_id).set(payload, merge=True)

