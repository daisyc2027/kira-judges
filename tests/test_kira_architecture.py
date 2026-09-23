import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    import app as app_module
except ModuleNotFoundError as exc:
    app_module = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


class FakeChatProvider:
    def __init__(self):
        self.calls = []

    def generate(self, messages, model=None, max_tokens=300, temperature=None):
        self.calls.append({
            "messages": messages,
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
        })
        return "I hear you. That sounds like a lot to hold.\n\nEMOTION:anxiety"


class FakeCollection:
    def __init__(self):
        self.added = []

    def add(self, payload):
        self.added.append(payload)


class FakeDocumentReference:
    def __init__(self, db, collection_name, document_id):
        self.db = db
        self.collection_name = collection_name
        self.document_id = document_id

    def collection(self, collection_name):
        self.db.subcollections_requested.append(collection_name)
        return collection_name

    def delete(self):
        self.db.deleted_documents.append((self.collection_name, self.document_id))


class FakeRootCollection:
    def __init__(self, db, collection_name):
        self.db = db
        self.collection_name = collection_name

    def document(self, document_id):
        return FakeDocumentReference(self.db, self.collection_name, document_id)


class FakeDeleteDb:
    def __init__(self):
        self.subcollections_requested = []
        self.deleted_documents = []

    def collection(self, collection_name):
        return FakeRootCollection(self, collection_name)


def test_config(chat_model="server-selected-model"):
    return SimpleNamespace(
        chat_model=chat_model,
        sleep_model=chat_model,
        diet_model=chat_model,
        prompt_version="test-prompt",
        rag_version="test-rag",
        enable_rag=True,
        enable_session_cache=True,
        enable_safety_router=True,
        debug_chat=False,
        require_auth=False,
    )


class KiraArchitectureTests(unittest.TestCase):
    def setUp(self):
        if app_module is None:
            self.skipTest("Flask app dependencies are not installed in this Python environment: " + str(IMPORT_ERROR))
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()
        self.provider = FakeChatProvider()
        self.fake_collection = FakeCollection()

    def test_old_published_chat_request_still_works_with_server_model_config(self):
        with patch.object(app_module, "config", test_config("new-server-model")), \
             patch.object(app_module, "chat_provider", self.provider), \
             patch.object(app_module, "get_cycle_context_for_chat", return_value={"estimatedPhase": "luteal"}), \
             patch.object(app_module, "load_session", return_value={"summary": "", "retrievedFactIds": []}), \
             patch.object(app_module, "get_recent_messages", return_value=[]), \
             patch.object(app_module, "get_user_summary", return_value={
                 "emotion_patterns": {"most_common_emotion": None, "highest_risk_phase": None},
                 "effective_interventions": [],
                 "intervention_summary": {},
             }), \
             patch.object(app_module, "get_relevant_user_context", return_value={}), \
             patch.object(app_module, "user_collection", return_value=self.fake_collection), \
             patch.object(app_module, "update_user_summary"), \
             patch.object(app_module, "update_summary_if_needed"):
            response = self.client.post("/chat", json={
                "user_id": "published_user",
                "message": "I feel anxious today",
                "phase": "luteal",
                "cycle_day": 24,
            })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertIn("message", payload)
        self.assertIn("emotion", payload)
        self.assertIn("intervention_trigger", payload)
        self.assertNotIn("model", payload)
        self.assertEqual(self.provider.calls[0]["model"], "new-server-model")

    def test_versioned_chat_route_keeps_same_response_contract(self):
        with patch.object(app_module, "config", test_config("versioned-model")), \
             patch.object(app_module, "chat_provider", self.provider), \
             patch.object(app_module, "get_cycle_context_for_chat", return_value={"estimatedPhase": "unknown"}), \
             patch.object(app_module, "load_session", return_value={"summary": "", "retrievedFactIds": []}), \
             patch.object(app_module, "get_recent_messages", return_value=[]), \
             patch.object(app_module, "get_user_summary", return_value={
                 "emotion_patterns": {"most_common_emotion": None, "highest_risk_phase": None},
                 "effective_interventions": [],
                 "intervention_summary": {},
             }), \
             patch.object(app_module, "get_relevant_user_context", return_value={}), \
             patch.object(app_module, "user_collection", return_value=self.fake_collection), \
             patch.object(app_module, "update_user_summary"), \
             patch.object(app_module, "update_summary_if_needed"):
            response = self.client.post("/api/v1/chat", json={
                "user_id": "published_user",
                "message": "I feel off",
            })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertIn("message", payload)
        self.assertEqual(self.provider.calls[0]["model"], "versioned-model")

    def test_delete_account_deletes_user_subcollections_and_account_documents(self):
        fake_db = FakeDeleteDb()
        with patch.object(app_module, "config", test_config()), \
             patch.object(app_module, "db", fake_db), \
             patch.object(app_module, "delete_collection_documents", return_value=2) as delete_collection:
            response = self.client.post("/delete_account", json={"user_id": "daisy"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["deleted_documents"], 2 * len(app_module.USER_DATA_COLLECTIONS))
        self.assertEqual(delete_collection.call_count, len(app_module.USER_DATA_COLLECTIONS))
        self.assertEqual(fake_db.subcollections_requested, app_module.USER_DATA_COLLECTIONS)
        self.assertIn(("users", "daisy"), fake_db.deleted_documents)
        self.assertIn(("accounts", "daisy"), fake_db.deleted_documents)


if __name__ == "__main__":
    unittest.main()
