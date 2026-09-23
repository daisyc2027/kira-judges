import unittest

from kira_services.knowledge import KnowledgeRepository
from kira_services.retrieval_gate import classify_retrieval
from kira_services.safety import evaluate_safety


class KiraChatServiceTests(unittest.TestCase):
    def setUp(self):
        self.repo = KnowledgeRepository(fallback_path="knowledge_base/rag_chunks.jsonl")

    def test_no_retrieval_for_emotional_support(self):
        decision = classify_retrieval("I've had such an annoying day.")
        self.assertFalse(decision["needsScientificRetrieval"])
        self.assertEqual(decision["intent"], "emotional_support")

    def test_cycle_science_retrieval_for_irritability(self):
        decision = classify_retrieval("Is it common to feel irritable before a period?")
        self.assertTrue(decision["needsScientificRetrieval"])
        self.assertIn("mood", decision["topics"])
        facts = self.repo.retrieve("irritable before a period PMS mood", decision["topics"], limit=5)
        self.assertTrue(facts)
        self.assertTrue(any("Mood" in fact["metadata"]["topic"] for fact in facts))

    def test_followup_can_use_cached_fact(self):
        decision = classify_retrieval("Is irritability common before a period?")
        self.assertTrue(decision["needsScientificRetrieval"])
        self.assertIn("mood", decision["topics"])
        facts = self.repo.retrieve("irritability before period PMS", decision["topics"], limit=3)
        cached = self.repo.select_relevant_cached_facts(
            "Does that mean hormones definitely caused mine?",
            facts,
            limit=3,
        )
        self.assertTrue(cached)

    def test_different_topic_needs_new_retrieval(self):
        mood_decision = classify_retrieval("Why am I irritable before my period?")
        mood_facts = self.repo.retrieve("irritable before period", mood_decision["topics"], limit=3)
        sleep_decision = classify_retrieval("Can my cycle affect my sleep too?")
        cached = self.repo.select_relevant_cached_facts("Can my cycle affect my sleep too?", mood_facts)
        self.assertTrue(self.repo.needs_additional_retrieval("Can my cycle affect my sleep too?", cached, sleep_decision["topics"]))

    def test_pmdd_safety_and_retrieval(self):
        safety = evaluate_safety("Do I have PMDD?")
        decision = classify_retrieval("Do I have PMDD?")
        self.assertEqual(safety["level"], "support")
        self.assertTrue(safety["suppressCycleExplanation"])
        self.assertTrue(decision["needsScientificRetrieval"])
        self.assertIn("pms_pmdd", decision["topics"])

    def test_severe_symptoms_activate_safety(self):
        safety = evaluate_safety("I have severe period pain and very heavy bleeding.")
        self.assertEqual(safety["level"], "medical_attention")
        self.assertTrue(safety["suppressCycleExplanation"])

    def test_unsupported_phase_stereotype_retrieves_focus(self):
        decision = classify_retrieval("Am I less intelligent because I'm on my period?")
        self.assertTrue(decision["needsScientificRetrieval"])
        self.assertIn("cycle_basics", decision["topics"])

    def test_urge_surfing_for_angry_text_impulse(self):
        decision = classify_retrieval("I have the urge to send an angry text and I might snap.")
        self.assertTrue(decision["needsScientificRetrieval"])
        self.assertIn("urge_surfing", decision["topics"])


if __name__ == "__main__":
    unittest.main()
