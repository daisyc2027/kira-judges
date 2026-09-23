"""Run inherited tests with Firebase initialization isolated from real services."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

with patch.dict(os.environ, {"GOOGLE_CREDENTIALS": "{}"}), \
     patch("firebase_admin.credentials.Certificate", return_value=MagicMock()), \
     patch("firebase_admin.initialize_app"), \
     patch("firebase_admin.firestore.client", return_value=MagicMock()):
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)

sys.exit(0 if result.wasSuccessful() else 1)
