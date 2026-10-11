import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from Backend import main
from Backend.auth import hash_password
from Backend.repository import DatasetRepository


APPLICATION = {
    "GENDER": "M",
    "QUALIFICATION": "Higher education",
    "FAMILY_STATUS": "Single / not married",
    "OCCUPATION": "Core staff",
    "CONTRACT_TYPE": "Cash loans",
    "TOTAL_INCOME": 50000,
    "CREDIT_AMOUNT": 150000,
    "ANNUAL_LOAN_PAYMENT": 12000,
    "GOODS_PRICE": 150000,
    "AGE": 30,
    "YEARS_OF_EXPERIENCE": 5,
    "CREDIT_SCORE": 710,
    "CREDIT_HISTORY": 0,
}


class AnalystApplicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repository = DatasetRepository(Path(self.directory.name) / "analyst-apps.sqlite3")
        self.previous_repository = main.datasets
        main.datasets = self.repository
        self.client = TestClient(main.app)
        self.repository.create_user("app-analyst", hash_password("secret"), role="analyst")
        self.repository.create_user("app-reviewer", hash_password("secret"), role="reviewer")
        self.repository.create_user("other-analyst", hash_password("secret"), role="analyst")

    def tearDown(self):
        main.datasets = self.previous_repository
        self.directory.cleanup()

    def _headers(self, username):
        token = self.client.post("/auth/login", json={"username": username, "password": "secret"}).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def test_reviewer_list_is_scoped_and_minimal(self):
        response = self.client.get("/api/reviewers", headers=self._headers("app-analyst"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("app-reviewer", response.json()["reviewers"])
        self.assertNotIn("app-analyst", response.json()["reviewers"])
        self.assertNotIn("password_hash", response.text)
        self.assertEqual(self.client.get("/api/reviewers", headers=self._headers("app-reviewer")).status_code, 403)

    def test_create_assign_assess_persist_and_retrieve(self):
        headers = self._headers("app-analyst")
        created = self.client.post("/api/applications", headers=headers, json={"reviewer_username": "app-reviewer", "applicant_snapshot": APPLICATION})
        self.assertEqual(created.status_code, 200)
        application_id = created.json()["application_id"]
        self.assertEqual(created.json()["status"], "submitted")
        listed = self.client.get("/api/applications", headers=headers).json()["applications"]
        self.assertTrue(any(item["application_id"] == application_id for item in listed))
        assessment = self.client.post(f"/api/applications/{application_id}/assess", headers=headers)
        self.assertEqual(assessment.status_code, 200)
        prediction_id = assessment.json()["prediction_id"]
        predictions = self.client.get(f"/api/applications/{application_id}/predictions", headers=headers).json()["predictions"]
        self.assertEqual(len(predictions), 1)
        self.assertEqual(predictions[0]["prediction_id"], prediction_id)

    def test_invalid_reviewer_and_reviewer_creation_rejected(self):
        headers = self._headers("app-analyst")
        bad_role = self.client.post("/api/applications", headers=headers, json={"reviewer_username": "other-analyst", "applicant_snapshot": APPLICATION})
        self.assertEqual(bad_role.status_code, 400)
        missing = self.client.post("/api/applications", headers=headers, json={"reviewer_username": "ghost", "applicant_snapshot": APPLICATION})
        self.assertEqual(missing.status_code, 400)
        reviewer_attempt = self.client.post("/api/applications", headers=self._headers("app-reviewer"), json={"reviewer_username": "app-reviewer", "applicant_snapshot": APPLICATION})
        self.assertEqual(reviewer_attempt.status_code, 403)

    def test_analyst_isolation_and_repeat_assessment(self):
        headers = self._headers("app-analyst")
        created = self.client.post("/api/applications", headers=headers, json={"reviewer_username": "app-reviewer", "applicant_snapshot": APPLICATION}).json()
        application_id = created["application_id"]
        other = self.client.get("/api/applications", headers=self._headers("other-analyst")).json()["applications"]
        self.assertFalse(any(item["application_id"] == application_id for item in other))
        self.assertEqual(self.client.get(f"/api/applications/{application_id}", headers=self._headers("other-analyst")).status_code, 404)
        first = self.client.post(f"/api/applications/{application_id}/assess", headers=headers)
        second = self.client.post(f"/api/applications/{application_id}/assess", headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        predictions = self.client.get(f"/api/applications/{application_id}/predictions", headers=headers).json()["predictions"]
        self.assertEqual(len(predictions), 2)


if __name__ == "__main__":
    unittest.main()
