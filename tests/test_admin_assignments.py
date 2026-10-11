"""Phase 3B: local SQLite assignment authorization, races and persistence."""
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from Backend import main
from Backend.auth import hash_password, issue_token
from Backend.repository import AssignmentError, DatasetRepository
from Backend.schemas import LoginRequest


class AdminAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repository = DatasetRepository(Path(self.directory.name) / "assignments.sqlite3")
        self.previous_repository = main.datasets
        main.datasets = self.repository
        self.client = TestClient(main.app)
        self.headers = {}
        password_hash = hash_password("1234")
        for username, role in (("admin", "admin"), ("analyst", "analyst"), ("old", "reviewer"), ("new", "reviewer"), ("third", "reviewer")):
            self.repository.create_user(username, password_hash, role=role)
            token = issue_token(LoginRequest(username=username, password="1234"), self.repository)["access_token"]
            self.headers[username] = {"Authorization": f"Bearer {token}"}
        self.app_id = self.repository.create_application("analyst", "manual", {"original": "snapshot"}, assigned_reviewer="old")

    def tearDown(self):
        main.datasets = self.previous_repository
        self.directory.cleanup()

    def payload(self, **changes):
        result = {"reviewer_username": "new", "reason": "Workload balancing",
                  "expected_updated_at": self.repository.get_application(self.app_id)["updated_at"]}
        result.update(changes)
        return result

    def reassign(self, payload=None, user="admin", app_id=None):
        return self.client.patch(f"/api/admin/applications/{app_id or self.app_id}/assignment",
                                 headers=self.headers[user], json=payload if payload is not None else self.payload())

    def save_prediction(self):
        self.repository.ensure_model_version("fixture-model", {})
        return self.repository.persist_prediction_assessment(self.app_id, "fixture-model", {
            "default_probability": .05, "risk_score": 5, "trust_score": 95, "confidence": .9,
            "decision": "Auto Approve", "risk_tier": "Low Risk", "input_snapshot": {"original": "snapshot"},
        }, {"base_value": .1, "output_space": "raw", "feature_contributions": [{"feature": "CREDIT_SCORE", "impact": -.02}]})

    def test_admin_reassignment_and_persisted_audit(self):
        before = self.repository.get_application(self.app_id)
        response = self.reassign()
        self.assertEqual(response.status_code, 200, response.text)
        after = self.repository.get_application(self.app_id)
        for key in before:
            if key not in {"assigned_reviewer", "updated_at"}:
                self.assertEqual(before[key], after[key])
        audit = self.repository.list_audit_logs(self.app_id)
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["actor"], "admin")
        self.assertEqual(audit[0]["event_type"], "assignment_changed")
        self.assertEqual(audit[0]["old_value"], {"assigned_reviewer": "old"})
        self.assertEqual(audit[0]["new_value"], {"assigned_reviewer": "new", "reason": "Workload balancing"})
        self.assertEqual(audit[0]["created_at"], after["updated_at"])
        reopened = DatasetRepository(self.repository.path)
        self.assertEqual(reopened.list_audit_logs(self.app_id), audit)

    def test_non_admin_denied(self):
        for user in ("analyst", "old"):
            with self.subTest(user=user):
                self.assertEqual(self.reassign(user=user).status_code, 403)
                self.assertEqual(self.client.get("/api/admin/assignments", headers=self.headers[user]).status_code, 403)
        self.assertEqual(self.client.patch(f"/api/admin/applications/{self.app_id}/assignment", json=self.payload()).status_code, 401)

    def test_invalid_targets(self):
        for target, status in (("missing", 404), ("analyst", 422), ("admin", 422), ("old", 422), (" ", 422)):
            with self.subTest(target=target):
                self.assertEqual(self.reassign(self.payload(reviewer_username=target)).status_code, status)
        self.assertEqual(self.repository.list_audit_logs(self.app_id), [])

    def test_reason_and_version_required(self):
        for field in ("reason", "expected_updated_at"):
            payload = self.payload(); payload.pop(field)
            self.assertEqual(self.reassign(payload).status_code, 422)
        for reason in ("", "   ", "x" * 2001):
            self.assertEqual(self.reassign(self.payload(reason=reason)).status_code, 422)

    def test_unknown_application(self):
        self.assertEqual(self.reassign(app_id="missing").status_code, 404)

    def test_finalized_statuses_denied(self):
        for status in ("approved", "rejected"):
            self.repository.update_application_status(self.app_id, status)
            self.assertEqual(self.reassign().status_code, 409)

    def test_existing_final_review_denied_even_after_status_changes(self):
        prediction = self.save_prediction()
        self.repository.create_review(self.app_id, prediction, "old", "approved", "", None, False)
        self.repository.update_application_status(self.app_id, "pending_review")
        self.assertEqual(self.reassign().status_code, 409)
        page = self.client.get("/api/admin/assignments", headers=self.headers["admin"]).json()
        self.assertFalse(page["applications"][0]["can_reassign"])

    def test_prediction_shap_and_access_preserved(self):
        prediction_id = self.save_prediction()
        before_predictions = self.repository.list_predictions(self.app_id)
        before_shap = self.repository.get_shap_explanation(prediction_id)
        self.assertEqual(self.reassign().status_code, 200)
        self.assertEqual(self.repository.list_predictions(self.app_id), before_predictions)
        self.assertEqual(self.repository.get_shap_explanation(prediction_id), before_shap)
        endpoints = (f"/api/applications/{self.app_id}", f"/api/applications/{self.app_id}/predictions",
                     f"/api/applications/{self.app_id}/predictions/{prediction_id}/explanation")
        for endpoint in endpoints:
            self.assertEqual(self.client.get(endpoint, headers=self.headers["old"]).status_code, 404)
            self.assertEqual(self.client.get(endpoint, headers=self.headers["new"]).status_code, 200)
            self.assertEqual(self.client.get(endpoint, headers=self.headers["analyst"]).status_code, 200)
        for queue in ("assigned", "pending"):
            old = self.client.get(f"/api/reviewers/{queue}", headers=self.headers["old"]).json()["applications"]
            new = self.client.get(f"/api/reviewers/{queue}", headers=self.headers["new"]).json()["applications"]
            self.assertEqual(old, [])
            self.assertEqual([app["application_id"] for app in new], [self.app_id])
        self.assertEqual(self.client.post(f"/api/applications/{self.app_id}/reviews", headers=self.headers["old"],
                                         json={"prediction_id": prediction_id, "reviewer_decision": "approved"}).status_code, 404)

    def test_information_request_remains_pending_and_preserves_review(self):
        prediction = self.save_prediction()
        self.repository.finalize_review(self.app_id, prediction, "old", "request_information", "Need documents", "More information", True)
        reviews = self.repository.list_reviews(self.app_id)
        self.assertEqual(self.reassign().status_code, 200)
        self.assertEqual(self.repository.list_reviews(self.app_id), reviews)
        self.assertEqual(self.repository.get_application(self.app_id)["status"], "needs_information")
        pending = self.client.get("/api/reviewers/pending", headers=self.headers["new"]).json()["applications"]
        self.assertEqual([app["application_id"] for app in pending], [self.app_id])

    def test_stale_version_denied(self):
        payload = self.payload()
        self.assertEqual(self.reassign(payload).status_code, 200)
        self.assertEqual(self.reassign({**payload, "reviewer_username": "third"}).status_code, 409)
        self.assertEqual(len(self.repository.list_audit_logs(self.app_id)), 1)

    def test_simultaneous_assignment_has_one_winner(self):
        version = self.payload()["expected_updated_at"]
        barrier = threading.Barrier(2)
        def assign(target):
            barrier.wait()
            try:
                self.repository.reassign_application(self.app_id, "admin", target, "Balance", version)
                return 200
            except AssignmentError as exc:
                return exc.status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(assign, ("new", "third")))
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(len(self.repository.list_audit_logs(self.app_id)), 1)

    def test_review_started_before_reassignment_is_rejected(self):
        prediction = self.save_prediction()
        original = self.repository.finalize_review
        def race(*args, **kwargs):
            self.repository.reassign_application(self.app_id, "admin", "new", "Race", kwargs["expected_updated_at"])
            return original(*args, **kwargs)
        with patch.object(self.repository, "finalize_review", side_effect=race):
            result = self.client.post(f"/api/applications/{self.app_id}/reviews", headers=self.headers["old"],
                                      json={"prediction_id": prediction, "reviewer_decision": "approved"})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.repository.list_reviews(self.app_id), [])

    def test_final_review_wins_race_and_blocks_assignment(self):
        prediction = self.save_prediction()
        payload = self.payload()
        self.repository.finalize_review(self.app_id, prediction, "old", "approved", "", None, False)
        self.assertEqual(self.reassign(payload).status_code, 409)

    def test_audit_insert_failure_rolls_back_assignment(self):
        before = self.repository.get_application(self.app_id)
        with self.repository._connection() as connection:
            connection.execute("CREATE TRIGGER fail_assignment_audit BEFORE INSERT ON audit_logs BEGIN SELECT RAISE(ABORT, 'audit failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.reassign_application(self.app_id, "admin", "new", "Balance", before["updated_at"])
        self.assertEqual(self.repository.get_application(self.app_id), before)

    def test_unassigned_and_bounded_list(self):
        unassigned = self.repository.create_application("analyst", "manual", {}, assigned_reviewer=None)
        for offset in (0, 1):
            response = self.client.get(f"/api/admin/assignments?limit=1&offset={offset}", headers=self.headers["admin"])
            self.assertEqual(response.status_code, 200)
            page = response.json()
            self.assertEqual(page["total"], 2)
            self.assertEqual(len(page["applications"]), 1)
            self.assertEqual(len(page["reviewer_workload"]), 3)
        self.assertEqual(self.client.get("/api/admin/assignments?limit=101", headers=self.headers["admin"]).status_code, 422)
        self.assertEqual(self.client.get("/api/admin/assignments?offset=-1", headers=self.headers["admin"]).status_code, 422)
        payload = self.payload(expected_updated_at=self.repository.get_application(unassigned)["updated_at"])
        self.assertEqual(self.reassign(payload, app_id=unassigned).status_code, 200)

    def test_return_to_original_reviewer_does_not_accept_old_version(self):
        initial = self.payload()
        self.assertEqual(self.reassign(initial).status_code, 200)
        self.assertEqual(self.reassign(self.payload(reviewer_username="old")).status_code, 200)
        self.assertEqual(self.reassign(initial).status_code, 409)
