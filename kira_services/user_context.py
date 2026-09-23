from collections import Counter


def _quality_score(value):
    return {"poor": 1, "okay": 2, "good": 3}.get(str(value).lower(), 0)


def get_relevant_user_context(db, user_id, retrieval_decision):
    topics = set(retrieval_decision.get("topics") or [])
    intent = retrieval_decision.get("intent")
    if intent != "personal_pattern":
        return {}

    user_doc = db.collection("users").document(user_id).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    context = {}

    if "sleep" in topics:
        docs = (
            db.collection("users")
            .document(user_id)
            .collection("sleep_logs")
            .limit(30)
            .stream()
        )
        logs = [doc.to_dict() for doc in docs]
        luteal = [log for log in logs if log.get("phase") == "luteal"]
        all_scores = [_quality_score(log.get("quality")) for log in logs if _quality_score(log.get("quality"))]
        luteal_scores = [_quality_score(log.get("quality")) for log in luteal if _quality_score(log.get("quality"))]
        context["sleepPattern"] = {
            "logsAnalyzed": len(logs),
            "lateCycleLogs": len(luteal),
            "baselineAverageQuality": round(sum(all_scores) / len(all_scores), 2) if all_scores else None,
            "lateCycleAverageQuality": round(sum(luteal_scores) / len(luteal_scores), 2) if luteal_scores else None,
            "note": "Associations from user logs only; do not frame as causation.",
        }

    if "nutrition_cravings" in topics:
        docs = (
            db.collection("users")
            .document(user_id)
            .collection("diet_logs")
            .limit(30)
            .stream()
        )
        logs = [doc.to_dict() for doc in docs]
        cravings = [log.get("craving") for log in logs if log.get("craving")]
        context["cravingPattern"] = {
            "logsAnalyzed": len(logs),
            "commonCravings": [name for name, _ in Counter(cravings).most_common(5)],
            "note": "Associations from user logs only; do not frame as causation.",
        }

    context["cycleTracking"] = {
        "periodsTracked": len(user_data.get("period_dates", [])),
        "averageCycleLength": user_data.get("average_cycle_length"),
        "averagePeriodLength": user_data.get("average_period_length"),
    }
    return context

