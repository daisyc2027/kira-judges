import json
import unittest

from kira_services.context_builder import build_context


class ContextBuilderTests(unittest.TestCase):
    def test_prompt_prioritizes_listening_over_unsolicited_interventions(self):
        messages = build_context(
            message="I feel anxious today",
            safety={"level": "none"},
            retrieval_decision={"needsScientificRetrieval": False},
            recent_messages=[],
            session_summary="",
            cycle_context={},
            personal_context={},
            scientific_facts=[],
            user_summary={"emotion_patterns": {}, "effective_interventions": [], "intervention_summary": {}},
        )

        system = json.loads(messages[0]["content"])
        rules = system["kiraBehaviorRules"]

        self.assertIn("Your default mode is listening, not fixing.", rules)
        self.assertIn("Do not rush into advice", rules)
        self.assertIn("Do not add an intervention tag just because", rules)


if __name__ == "__main__":
    unittest.main()
