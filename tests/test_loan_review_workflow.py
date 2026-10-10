import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from Backend import main
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


class LoanReviewWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repository = DatasetRepository(Path(self.directory.name) / "workflow.sqlite3")
        self.previous_repository = main.datasets
        main.datasets = self.repository
        self.client = TestClient(main.app)

    def tearDown(self):
        main.datasets = self.previous_repository
        self.directory.cleanup()

    def _register(self, username):
        response = self.client.post(
            "/auth/register", json={"username": username, "password": "secret"}
        )
        self.assertEqual(response.status_code, 200)
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def test_workflow_persists_prediction_explanation_override_and_audit(self):
        analyst_headers = self._register("workflow-analyst")
        reviewer_headers = self._register("workflow-reviewer")
        self.repository.set_user_role("workflow-reviewer", "reviewer")

        created = self.client.post(
            "/api/applications",
            headers=analyst_headers,
            json={
                "source_type": "manual",
                "reviewer_username": "workflow-reviewer",
                "applicant_snapshot": APPLICATION,
            },
        )
        self.assertEqual(created.status_code, 200)
        application_id = created.json()["application_id"]

        assessment = self.client.post(
            f"/api/applications/{application_id}/assess", headers=analyst_headers
        )
        self.assertEqual(assessment.status_code, 200)
        prediction_id = assessment.json()["prediction_id"]
        self.assertEqual(len(self.repository.list_predictions(application_id)), 1)

        prediction = self.repository.list_predictions(application_id)[0]
        self.assertGreaterEqual(prediction["default_probability"], 0)
        self.assertLessEqual(prediction["default_probability"], 1)

        model_decision = assessment.json()["result"]["decision"]
        reviewer_decision = "rejected" if model_decision != "Auto Decline" else "approved"
        review = self.client.post(
            f"/api/applications/{application_id}/reviews",
            headers=reviewer_headers,
            json={
                "prediction_id": prediction_id,
                "reviewer_decision": reviewer_decision,
                "review_notes": "Manual assessment differs from the model.",
                "override_reason": "Additional income documentation reviewed.",
            },
        )
        self.assertEqual(review.status_code, 200)
        self.assertTrue(review.json()["is_override"])

        reopened = DatasetRepository(Path(self.directory.name) / "workflow.sqlite3")
        self.assertEqual(reopened.get_application(application_id)["status"], "rejected")
        self.assertEqual(len(reopened.list_predictions(application_id)), 1)
        self.assertEqual(len(reopened.list_reviews(application_id)), 1)
        self.assertEqual(len(reopened.list_audit_logs(application_id)), 3)

    def test_roles_assignment_and_state_validation_are_enforced(self):
        analyst_headers = self._register("role-analyst")
        reviewer_headers = self._register("role-reviewer")
        other_reviewer_headers = self._register("role-other")
        self.repository.set_user_role("role-reviewer", "reviewer")
        self.repository.set_user_role("role-other", "reviewer")

        invalid = self.client.post(
            "/api/applications",
            headers=analyst_headers,
            json={"applicant_snapshot": {"GENDER": "M"}},
        )
        self.assertEqual(invalid.status_code, 422)

        created = self.client.post(
            "/api/applications",
            headers=analyst_headers,
            json={
                "reviewer_username": "role-reviewer",
                "applicant_snapshot": APPLICATION,
            },
        )
        application_id = created.json()["application_id"]
        self.assertEqual(
            self.client.post(
                f"/api/applications/{application_id}/reviews",
                headers=analyst_headers,
                json={"reviewer_decision": "approved"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                f"/api/applications/{application_id}", headers=other_reviewer_headers
            ).status_code,
            404,
        )

        assessment = self.client.post(
            f"/api/applications/{application_id}/assess", headers=analyst_headers
        )
        self.assertEqual(assessment.status_code, 200)
        reviewer_decision = "approved"
        if assessment.json()["result"]["decision"] == "Auto Approve":
            reviewer_decision = "rejected"
        review_payload = {
            "prediction_id": assessment.json()["prediction_id"],
            "reviewer_decision": reviewer_decision,
        }
        self.assertEqual(
            self.client.post(
                f"/api/applications/{application_id}/reviews",
                headers=reviewer_headers,
                json=review_payload,
            ).status_code,
            422,
        )
        review_payload["override_reason"] = "Documented manual exception."
        self.assertEqual(
            self.client.post(
                f"/api/applications/{application_id}/reviews",
                headers=reviewer_headers,
                json=review_payload,
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                f"/api/applications/{application_id}/reviews",
                headers=reviewer_headers,
                json=review_payload,
            ).status_code,
            409,
        )

    def test_concurrent_reviews_only_one_can_finalize(self):
        analyst_headers = self._register("concurrent-analyst")
        reviewer_headers = self._register("concurrent-reviewer")
        self.repository.set_user_role("concurrent-reviewer", "reviewer")
        created = self.client.post(
            "/api/applications",
            headers=analyst_headers,
            json={
                "reviewer_username": "concurrent-reviewer",
                "applicant_snapshot": APPLICATION,
            },
        )
        application_id = created.json()["application_id"]
        assessment = self.client.post(
            f"/api/applications/{application_id}/assess", headers=analyst_headers
        )
        payload = {
            "prediction_id": assessment.json()["prediction_id"],
            "reviewer_decision": "rejected",
            "override_reason": "Concurrent test decision.",
        }

        def submit():
            with TestClient(main.app) as client:
                return client.post(
                    f"/api/applications/{application_id}/reviews",
                    headers=reviewer_headers,
                    json=payload,
                ).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(lambda _: submit(), range(2)))

        self.assertEqual(sorted(statuses), [200, 409])
        self.assertEqual(len(self.repository.list_reviews(application_id)), 1)
