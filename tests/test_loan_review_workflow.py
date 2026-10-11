import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from Backend import main
from Backend.repository import DatasetRepository
from Backend.auth import hash_password


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
        self.repository.create_user(username, hash_password("secret"), role="analyst")
        response = self.client.post(
            "/auth/login", json={"username": username, "password": "secret"}
        )
        self.assertEqual(response.status_code, 200)
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def test_workflow_persists_prediction_explanation_override_and_audit(self):
        analyst_headers = self._register("workflow-analyst")
        reviewer_headers = self._register("workflow-reviewer")
        self.repository.set_user_role("workflow-reviewer", "reviewer")
        reviewer_headers = {"Authorization": "Bearer " + self.client.post("/auth/login", json={"username": "workflow-reviewer", "password": "secret"}).json()["access_token"]}

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
        reviewer_headers = {"Authorization": "Bearer " + self.client.post("/auth/login", json={"username": "role-reviewer", "password": "secret"}).json()["access_token"]}
        self.repository.set_user_role("role-other", "reviewer")
        other_reviewer_headers = {"Authorization": "Bearer " + self.client.post("/auth/login", json={"username": "role-other", "password": "secret"}).json()["access_token"]}

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
        reviewer_headers = {"Authorization": "Bearer " + self.client.post("/auth/login", json={"username": "concurrent-reviewer", "password": "secret"}).json()["access_token"]}
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

    def test_reviewer_queues_assigned_pending_history_and_isolation(self):
        analyst_headers = self._register("q-analyst")
        rev1_headers = self._register("q-reviewer-1")
        rev2_headers = self._register("q-reviewer-2")
        self.repository.set_user_role("q-reviewer-1", "reviewer")
        rev1_headers = {"Authorization": "Bearer " + self.client.post("/auth/login", json={"username": "q-reviewer-1", "password": "secret"}).json()["access_token"]}
        self.repository.set_user_role("q-reviewer-2", "reviewer")
        rev2_headers = {"Authorization": "Bearer " + self.client.post("/auth/login", json={"username": "q-reviewer-2", "password": "secret"}).json()["access_token"]}

        # Create two applications assigned to different reviewers
        app1_res = self.client.post(
            "/api/applications",
            headers=analyst_headers,
            json={"reviewer_username": "q-reviewer-1", "applicant_snapshot": APPLICATION},
        )
        self.assertEqual(app1_res.status_code, 200)
        app1_id = app1_res.json()["application_id"]

        app2_res = self.client.post(
            "/api/applications",
            headers=analyst_headers,
            json={"reviewer_username": "q-reviewer-2", "applicant_snapshot": APPLICATION},
        )
        self.assertEqual(app2_res.status_code, 200)
        app2_id = app2_res.json()["application_id"]

        # Assess app1
        assess_res = self.client.post(f"/api/applications/{app1_id}/assess", headers=analyst_headers)
        self.assertEqual(assess_res.status_code, 200)
        pred_id = assess_res.json()["prediction_id"]

        # Verify assigned queue isolation
        rev1_assigned = self.client.get("/api/reviewers/assigned", headers=rev1_headers).json()["applications"]
        self.assertEqual(len(rev1_assigned), 1)
        self.assertEqual(rev1_assigned[0]["application_id"], app1_id)

        rev2_assigned = self.client.get("/api/reviewers/assigned", headers=rev2_headers).json()["applications"]
        self.assertEqual(len(rev2_assigned), 1)
        self.assertEqual(rev2_assigned[0]["application_id"], app2_id)

        # Verify pending queue
        rev1_pending = self.client.get("/api/reviewers/pending", headers=rev1_headers).json()["applications"]
        self.assertEqual(len(rev1_pending), 1)
        self.assertEqual(rev1_pending[0]["application_id"], app1_id)

        # Verify history is initially empty
        rev1_history = self.client.get("/api/reviewers/history", headers=rev1_headers).json()["history"]
        self.assertEqual(len(rev1_history), 0)

        # Submit review from rev1
        model_decision = assess_res.json()["result"]["decision"]
        expected_decision = {
            "Auto Approve": "approved",
            "Auto Decline": "rejected",
            "Manual Review Required": "request_information",
        }.get(model_decision)
        decision = "approved"
        override_reason = "Override rationale" if decision != expected_decision else None
        review_res = self.client.post(
            f"/api/applications/{app1_id}/reviews",
            headers=rev1_headers,
            json={
                "prediction_id": pred_id,
                "reviewer_decision": decision,
                "review_notes": "Queue test note",
                "override_reason": override_reason,
            },
        )
        self.assertEqual(review_res.status_code, 200)

        # Now rev1 pending queue must be empty
        rev1_pending_after = self.client.get("/api/reviewers/pending", headers=rev1_headers).json()["applications"]
        self.assertEqual(len(rev1_pending_after), 0)

        # Rev1 history must contain app1
        rev1_history_after = self.client.get("/api/reviewers/history", headers=rev1_headers).json()["history"]
        self.assertEqual(len(rev1_history_after), 1)
        self.assertEqual(rev1_history_after[0]["application_id"], app1_id)
        self.assertEqual(rev1_history_after[0]["final_decision"], decision)
        self.assertEqual(rev1_history_after[0]["notes"], "Queue test note")

        # Rev2 history remains empty
        rev2_history = self.client.get("/api/reviewers/history", headers=rev2_headers).json()["history"]
        self.assertEqual(len(rev2_history), 0)

        # Analyst cannot view reviewer pending queue
        analyst_pending = self.client.get("/api/reviewers/pending", headers=analyst_headers).json()["applications"]
        self.assertEqual(len(analyst_pending), 0)

        # Audit logs persist
        audit_logs = self.repository.list_audit_logs(app1_id)
        self.assertTrue(any(log["event_type"] == "manual_review" for log in audit_logs))

    def test_reviewer_direct_agreement_and_override_semantics(self):
        analyst_headers = self._register("agree-analyst")
        reviewer_headers = self._register("agree-reviewer")
        self.repository.set_user_role("agree-reviewer", "reviewer")
        reviewer_headers = {"Authorization": "Bearer " + self.client.post("/auth/login", json={"username": "agree-reviewer", "password": "secret"}).json()["access_token"]}

        created = self.client.post(
            "/api/applications",
            headers=analyst_headers,
            json={"reviewer_username": "agree-reviewer", "applicant_snapshot": APPLICATION},
        )
        app_id = created.json()["application_id"]
        assessment = self.client.post(f"/api/applications/{app_id}/assess", headers=analyst_headers).json()
        model_decision = assessment["result"]["decision"]
        pred_id = assessment["prediction_id"]

        if model_decision in ("Auto Approve", "Auto Decline"):
            agreeing_decision = "approved" if model_decision == "Auto Approve" else "rejected"
            # Agreement without override reason succeeds with is_override = False
            review_res = self.client.post(
                f"/api/applications/{app_id}/reviews",
                headers=reviewer_headers,
                json={
                    "prediction_id": pred_id,
                    "reviewer_decision": agreeing_decision,
                    "review_notes": "Agreeing with model recommendation",
                },
            )
            self.assertEqual(review_res.status_code, 200)
            self.assertFalse(review_res.json()["is_override"])
        else:
            # "Manual Review Required" - any approval or rejection is treated as override of request_information
            review_res = self.client.post(
                f"/api/applications/{app_id}/reviews",
                headers=reviewer_headers,
                json={
                    "prediction_id": pred_id,
                    "reviewer_decision": "approved",
                },
            )
            self.assertEqual(review_res.status_code, 422)
            review_res_ok = self.client.post(
                f"/api/applications/{app_id}/reviews",
                headers=reviewer_headers,
                json={
                    "prediction_id": pred_id,
                    "reviewer_decision": "approved",
                    "override_reason": "Manual exception rationale",
                },
            )
            self.assertEqual(review_res_ok.status_code, 200)
            self.assertTrue(review_res_ok.json()["is_override"])

    def test_reviewer_shap_explanation_retrieval_and_authorization(self):
        analyst_headers = self._register("shap-analyst")
        other_analyst_headers = self._register("shap-other-analyst")
        assigned_reviewer_headers = self._register("shap-assigned-rev")
        unassigned_reviewer_headers = self._register("shap-unassigned-rev")
        self.repository.set_user_role("shap-assigned-rev", "reviewer")
        assigned_reviewer_headers = {"Authorization": "Bearer " + self.client.post("/auth/login", json={"username": "shap-assigned-rev", "password": "secret"}).json()["access_token"]}
        self.repository.set_user_role("shap-unassigned-rev", "reviewer")
        unassigned_reviewer_headers = {"Authorization": "Bearer " + self.client.post("/auth/login", json={"username": "shap-unassigned-rev", "password": "secret"}).json()["access_token"]}

        # Create application assigned to shap-assigned-rev
        app_res = self.client.post(
            "/api/applications",
            headers=analyst_headers,
            json={"reviewer_username": "shap-assigned-rev", "applicant_snapshot": APPLICATION},
        )
        self.assertEqual(app_res.status_code, 200)
        app_id = app_res.json()["application_id"]

        # Assess application
        assess_res = self.client.post(f"/api/applications/{app_id}/assess", headers=analyst_headers)
        self.assertEqual(assess_res.status_code, 200)
        pred_id = assess_res.json()["prediction_id"]

        # 1. Assigned reviewer can retrieve SHAP explanation
        expl_res = self.client.get(
            f"/api/applications/{app_id}/predictions/{pred_id}/explanation",
            headers=assigned_reviewer_headers,
        )
        self.assertEqual(expl_res.status_code, 200)
        explanation = expl_res.json()["explanation"]
        self.assertEqual(explanation["prediction_id"], pred_id)
        self.assertIn("feature_contributions", explanation)
        self.assertTrue(len(explanation["feature_contributions"]) > 0)
        self.assertIn("output_space", explanation)
        self.assertIn("base_value", explanation)

        # Also test /shap alias
        shap_alias_res = self.client.get(
            f"/api/applications/{app_id}/predictions/{pred_id}/shap",
            headers=assigned_reviewer_headers,
        )
        self.assertEqual(shap_alias_res.status_code, 200)

        # 2. Unassigned reviewer CANNOT retrieve SHAP explanation (HTTP 404)
        unassigned_res = self.client.get(
            f"/api/applications/{app_id}/predictions/{pred_id}/explanation",
            headers=unassigned_reviewer_headers,
        )
        self.assertEqual(unassigned_res.status_code, 404)

        # 3. Non-owner analyst CANNOT retrieve SHAP explanation (HTTP 404)
        non_owner_res = self.client.get(
            f"/api/applications/{app_id}/predictions/{pred_id}/explanation",
            headers=other_analyst_headers,
        )
        self.assertEqual(non_owner_res.status_code, 404)

        # 4. Owner analyst CAN retrieve SHAP explanation
        owner_res = self.client.get(
            f"/api/applications/{app_id}/predictions/{pred_id}/explanation",
            headers=analyst_headers,
        )
        self.assertEqual(owner_res.status_code, 200)

        # 5. Non-existent prediction_id returns 404
        missing_pred_res = self.client.get(
            f"/api/applications/{app_id}/predictions/nonexistent_pred/explanation",
            headers=assigned_reviewer_headers,
        )
        self.assertEqual(missing_pred_res.status_code, 404)

        # 6. Prediction without saved explanation returns 404
        dummy_pred_id = "dummy_pred_without_shap"
        existing_model_id = self.repository.list_predictions(app_id)[0]["model_id"]
        with self.repository._connection() as conn:
            conn.execute(
                self.repository._sql(
                    "INSERT INTO ml_predictions (prediction_id, application_id, model_id, default_probability, risk_score, trust_score, confidence, predicted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                ),
                (dummy_pred_id, app_id, existing_model_id, 0.15, 15.0, 85.0, 0.95, "2026-10-11T00:00:00Z"),
            )
        missing_expl_res = self.client.get(
            f"/api/applications/{app_id}/predictions/{dummy_pred_id}/explanation",
            headers=assigned_reviewer_headers,
        )
        self.assertEqual(missing_expl_res.status_code, 404)
