"""Tests for GET /api/admin/overview — Admin Portfolio Oversight (Phase 3A)."""
import tempfile
import unittest
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


class AdminOverviewTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repository = DatasetRepository(Path(self.directory.name) / "admin_test.sqlite3")
        self.previous_repository = main.datasets
        main.datasets = self.repository
        self.client = TestClient(main.app)

    def tearDown(self):
        main.datasets = self.previous_repository
        self.directory.cleanup()

    def _register(self, username, role="analyst"):
        self.repository.create_user(username, hash_password("secret"), role="analyst")
        response = self.client.post(
            "/auth/login", json={"username": username, "password": "secret"}
        )
        self.assertEqual(response.status_code, 200)
        if role != "analyst":
            self.repository.set_user_role(username, role)
            response = self.client.post("/auth/login", json={"username": username, "password": "secret"})
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    # ------------------------------------------------------------------
    # TASK 1 — Authorization
    # ------------------------------------------------------------------

    def test_admin_can_access_overview(self):
        """Admin role gets HTTP 200 from /api/admin/overview."""
        headers = self._register("admin1", "admin")
        response = self.client.get("/api/admin/overview", headers=headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("totals", body)
        self.assertIn("reviewer_workload", body)

    def test_analyst_denied_admin_overview(self):
        """Analyst role gets HTTP 403 from /api/admin/overview."""
        headers = self._register("analyst1", "analyst")
        response = self.client.get("/api/admin/overview", headers=headers)
        self.assertEqual(response.status_code, 403)

    def test_reviewer_denied_admin_overview(self):
        """Reviewer role gets HTTP 403 from /api/admin/overview."""
        headers = self._register("reviewer1", "reviewer")
        response = self.client.get("/api/admin/overview", headers=headers)
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_denied_admin_overview(self):
        """No token returns HTTP 401 from /api/admin/overview."""
        response = self.client.get("/api/admin/overview")
        self.assertEqual(response.status_code, 401)

    # ------------------------------------------------------------------
    # TASK 2 — Empty database
    # ------------------------------------------------------------------

    def test_empty_database_returns_zero_metrics(self):
        """With no applications the totals must all be zero."""
        headers = self._register("admin2", "admin")
        response = self.client.get("/api/admin/overview", headers=headers)
        self.assertEqual(response.status_code, 200)
        totals = response.json()["totals"]
        self.assertEqual(totals["total"], 0)
        self.assertEqual(totals["approved"], 0)
        self.assertEqual(totals["rejected"], 0)
        self.assertEqual(totals["pending"], 0)
        self.assertEqual(totals["awaiting_assessment"], 0)

    def test_empty_database_reviewer_workload_uses_registered_reviewers(self):
        """Reviewer workload includes registered reviewers even with zero apps."""
        headers = self._register("admin3", "admin")
        self._register("reviewer_x", "reviewer")
        response = self.client.get("/api/admin/overview", headers=headers)
        self.assertEqual(response.status_code, 200)
        workload = response.json()["reviewer_workload"]
        usernames = [r["username"] for r in workload]
        self.assertIn("reviewer_x", usernames)
        entry = next(r for r in workload if r["username"] == "reviewer_x")
        self.assertEqual(entry["total_assigned"], 0)
        self.assertEqual(entry["pending"], 0)
        self.assertEqual(entry["finalized"], 0)

    # ------------------------------------------------------------------
    # TASK 3 — Created applications appear in counts
    # ------------------------------------------------------------------

    def test_created_application_increments_total(self):
        """Creating an application increments total and awaiting_assessment."""
        admin_hdr = self._register("admin4", "admin")
        analyst_hdr = self._register("analyst4", "analyst")
        resp = self.client.post(
            "/api/applications",
            headers=analyst_hdr,
            json={"source_type": "manual", "applicant_snapshot": APPLICATION},
        )
        self.assertEqual(resp.status_code, 200)

        overview = self.client.get("/api/admin/overview", headers=admin_hdr).json()
        totals = overview["totals"]
        self.assertEqual(totals["total"], 1)
        self.assertEqual(totals["submitted"], 1)
        self.assertEqual(totals["awaiting_assessment"], 1)

    def test_assessed_application_moves_to_pending(self):
        """After ML assessment the application moves to pending_review status."""
        admin_hdr = self._register("admin5", "admin")
        analyst_hdr = self._register("analyst5", "analyst")
        created = self.client.post(
            "/api/applications",
            headers=analyst_hdr,
            json={"source_type": "manual", "applicant_snapshot": APPLICATION},
        )
        app_id = created.json()["application_id"]
        self.client.post(f"/api/applications/{app_id}/assess", headers=analyst_hdr)

        overview = self.client.get("/api/admin/overview", headers=admin_hdr).json()
        totals = overview["totals"]
        self.assertEqual(totals["pending_review"], 1)
        self.assertEqual(totals["submitted"], 0)

    def test_approved_application_counts(self):
        """Approving an application is reflected in the approved count."""
        admin_hdr = self._register("admin6", "admin")
        analyst_hdr = self._register("analyst6", "analyst")
        reviewer_hdr = self._register("reviewer6", "reviewer")

        created = self.client.post(
            "/api/applications",
            headers=analyst_hdr,
            json={
                "source_type": "manual",
                "reviewer_username": "reviewer6",
                "applicant_snapshot": APPLICATION,
            },
        )
        app_id = created.json()["application_id"]
        assess = self.client.post(f"/api/applications/{app_id}/assess", headers=analyst_hdr)
        prediction_id = assess.json()["prediction_id"]

        model_decision = assess.json()["result"]["decision"]
        reviewer_choice = "approved" if model_decision == "Auto Approve" else "rejected"
        # Use a decision that IS the same as the model to avoid override requirement
        if model_decision == "Auto Approve":
            reviewer_decision = "approved"
        elif model_decision == "Auto Decline":
            reviewer_decision = "rejected"
        else:
            reviewer_decision = "approved"
            # Manual Review Required — override needed
            override_reason = "Analyst recommended approval"

        payload = {
            "prediction_id": prediction_id,
            "reviewer_decision": reviewer_decision,
            "review_notes": "test",
            "override_reason": override_reason if model_decision == "Manual Review Required" else None,
        }
        review_resp = self.client.post(
            f"/api/applications/{app_id}/reviews", headers=reviewer_hdr, json=payload
        )
        self.assertEqual(review_resp.status_code, 200)

        overview = self.client.get("/api/admin/overview", headers=admin_hdr).json()
        totals = overview["totals"]
        if reviewer_decision == "approved":
            self.assertEqual(totals["approved"], 1)
            self.assertEqual(totals["rejected"], 0)
        else:
            self.assertEqual(totals["rejected"], 1)
            self.assertEqual(totals["approved"], 0)

    def test_rejected_application_counts(self):
        """Rejecting an application is reflected in the rejected count."""
        admin_hdr = self._register("admin7", "admin")
        analyst_hdr = self._register("analyst7", "analyst")
        reviewer_hdr = self._register("reviewer7", "reviewer")

        created = self.client.post(
            "/api/applications",
            headers=analyst_hdr,
            json={
                "source_type": "manual",
                "reviewer_username": "reviewer7",
                "applicant_snapshot": APPLICATION,
            },
        )
        app_id = created.json()["application_id"]
        assess = self.client.post(f"/api/applications/{app_id}/assess", headers=analyst_hdr)
        prediction_id = assess.json()["prediction_id"]
        model_decision = assess.json()["result"]["decision"]
        # Choose rejection; provide override if model said Auto Approve
        reviewer_decision = "rejected"
        override_reason = "Risk is too high" if model_decision == "Auto Approve" else None

        payload = {
            "prediction_id": prediction_id,
            "reviewer_decision": reviewer_decision,
            "review_notes": "",
            "override_reason": override_reason,
        }
        rev = self.client.post(
            f"/api/applications/{app_id}/reviews", headers=reviewer_hdr, json=payload
        )
        self.assertEqual(rev.status_code, 200)

        overview = self.client.get("/api/admin/overview", headers=admin_hdr).json()
        self.assertEqual(overview["totals"]["rejected"], 1)

    # ------------------------------------------------------------------
    # TASK 4 — Reviewer workload
    # ------------------------------------------------------------------

    def test_reviewer_workload_counts_assigned(self):
        """Assigning an application to a reviewer increments their workload."""
        admin_hdr = self._register("admin8", "admin")
        analyst_hdr = self._register("analyst8", "analyst")
        self._register("reviewer8", "reviewer")

        self.client.post(
            "/api/applications",
            headers=analyst_hdr,
            json={
                "source_type": "manual",
                "reviewer_username": "reviewer8",
                "applicant_snapshot": APPLICATION,
            },
        )

        overview = self.client.get("/api/admin/overview", headers=admin_hdr).json()
        workload = overview["reviewer_workload"]
        entry = next((r for r in workload if r["username"] == "reviewer8"), None)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["total_assigned"], 1)
        self.assertEqual(entry["pending"], 1)
        self.assertEqual(entry["finalized"], 0)

    def test_unassigned_applications_count(self):
        """Applications with no reviewer appear in unassigned_count."""
        admin_hdr = self._register("admin9", "admin")
        analyst_hdr = self._register("analyst9", "analyst")

        # Create without reviewer
        self.client.post(
            "/api/applications",
            headers=analyst_hdr,
            json={"source_type": "manual", "applicant_snapshot": APPLICATION},
        )

        overview = self.client.get("/api/admin/overview", headers=admin_hdr).json()
        self.assertGreaterEqual(overview["unassigned_count"], 1)

    def test_finalized_application_updates_reviewer_workload(self):
        """After approval the reviewer's finalized count increments."""
        admin_hdr = self._register("admin10", "admin")
        analyst_hdr = self._register("analyst10", "analyst")
        reviewer_hdr = self._register("reviewer10", "reviewer")

        created = self.client.post(
            "/api/applications",
            headers=analyst_hdr,
            json={
                "source_type": "manual",
                "reviewer_username": "reviewer10",
                "applicant_snapshot": APPLICATION,
            },
        )
        app_id = created.json()["application_id"]
        assess = self.client.post(f"/api/applications/{app_id}/assess", headers=analyst_hdr)
        prediction_id = assess.json()["prediction_id"]
        model_decision = assess.json()["result"]["decision"]
        reviewer_decision = "approved" if model_decision != "Auto Decline" else "rejected"
        override_reason = "approved due to borderline case" if model_decision == "Auto Decline" else None

        payload = {
            "prediction_id": prediction_id,
            "reviewer_decision": reviewer_decision,
            "review_notes": "",
            "override_reason": override_reason,
        }
        self.client.post(f"/api/applications/{app_id}/reviews", headers=reviewer_hdr, json=payload)

        overview = self.client.get("/api/admin/overview", headers=admin_hdr).json()
        workload = overview["reviewer_workload"]
        entry = next((r for r in workload if r["username"] == "reviewer10"), None)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["finalized"], 1)
        self.assertEqual(entry["pending"], 0)

    # ------------------------------------------------------------------
    # TASK 5 — Response shape
    # ------------------------------------------------------------------

    def test_overview_response_shape(self):
        """Response always contains required top-level keys."""
        headers = self._register("admin11", "admin")
        response = self.client.get("/api/admin/overview", headers=headers)
        body = response.json()
        for key in ("totals", "status_distribution", "reviewer_workload", "unassigned_count", "recent_applications"):
            self.assertIn(key, body, f"Key '{key}' missing from overview response")

    def test_recent_applications_bounded(self):
        """recent_applications list must not exceed 10 entries."""
        admin_hdr = self._register("admin12", "admin")
        analyst_hdr = self._register("analyst12", "analyst")
        for _ in range(12):
            self.client.post(
                "/api/applications",
                headers=analyst_hdr,
                json={"source_type": "manual", "applicant_snapshot": APPLICATION},
            )
        overview = self.client.get("/api/admin/overview", headers=admin_hdr).json()
        self.assertLessEqual(len(overview["recent_applications"]), 10)


if __name__ == "__main__":
    unittest.main()
