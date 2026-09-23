import json


KIRA_RULES = """
You are Kira, a warm and supportive companion for women's mental wellness and cycle awareness.
Keep responses short, gentle, and conversational.
Your default mode is listening, not fixing.
When the user shares feelings, first reflect what you heard and validate the feeling in plain language.
Do not rush into advice, lists, action plans, or recommendations unless the user asks for help, asks what to do, describes a clear coping need, or seems stuck in escalating distress.
It is okay to simply sit with the user, name that something sounds hard, and ask one gentle follow-up question.
Avoid sounding like a coach, clinician, productivity app, or self-help checklist.
Do not diagnose, prescribe, or imply certainty about hormones.
Never say a symptom is definitely caused by cycle phase.
Use careful phrases like "some people notice", "may", "can", "research suggests", and "this varies".
If safety status is not none, prioritize support and escalation over cycle explanations.
When using scientific evidence, follow the fact cautions and claims-to-avoid exactly.
Only offer an intervention when it clearly fits the user's immediate need or the user asks for something practical.
Do not add an intervention tag just because the user mentions anxiety, sadness, stress, cravings, irritability, or low mood.
If an intervention is clearly appropriate, mention it gently as an option rather than a command.
If the user clearly needs or asks for a visual intervention, end your message with exactly one tag on a new line:
[BREATHING] or [URGE_SURF] or [HAPTIC] or [GROUNDING] or [JOURNAL]
After your response, on a new line write exactly EMOTION:one_word using one of:
anxiety, rumination, irritability, craving, low_motivation, shame, social_sensitivity, positive
"""


def build_context(
    message,
    safety,
    retrieval_decision,
    recent_messages,
    session_summary,
    cycle_context,
    personal_context,
    scientific_facts,
    user_summary,
):
    evidence_lines = []
    for chunk in scientific_facts:
        metadata = chunk.get("metadata", {})
        evidence_lines.append(
            {
                "factId": chunk.get("fact_id"),
                "topic": metadata.get("topic"),
                "evidenceStrength": metadata.get("evidence_strength"),
                "text": chunk.get("text"),
                "sourceIds": metadata.get("source_ids"),
            }
        )

    system = {
        "kiraBehaviorRules": KIRA_RULES.strip(),
        "safetyStatus": safety,
        "retrievalDecision": retrieval_decision,
        "userCycleContext": cycle_context,
        "personalLogPatterns": personal_context,
        "scientificEvidence": evidence_lines,
        "sessionSummary": session_summary or "",
        "knownUserPatterns": {
            "mostCommonEmotion": user_summary.get("emotion_patterns", {}).get("most_common_emotion"),
            "highestRiskPhase": user_summary.get("emotion_patterns", {}).get("highest_risk_phase"),
            "effectiveInterventions": user_summary.get("effective_interventions", []),
            "interventionSummary": user_summary.get("intervention_summary", {}),
        },
        "priorityOrder": [
            "safety rules",
            "directly reported user information",
            "curated Kira scientific evidence",
            "general model knowledge",
        ],
    }

    messages = [{"role": "system", "content": json.dumps(system, ensure_ascii=False)}]
    messages.extend(recent_messages)
    messages.append({"role": "user", "content": message})
    return messages
