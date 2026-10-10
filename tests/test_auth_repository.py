import tempfile
import unittest
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1] / "Backend"))
from Backend.auth import hash_password, issue_token, verify_password
from Backend.repository import DatasetRepository
from Backend.schemas import LoginRequest


class AuthRepositoryTests(unittest.TestCase):
    def test_password_hashes_are_salted_and_verifiable(self):
        password = "correct horse battery staple"
        encoded = hash_password(password)

        self.assertNotEqual(encoded, password)
        self.assertNotEqual(encoded, hash_password(password))
        self.assertTrue(verify_password(password, encoded))
        self.assertFalse(verify_password("wrong", encoded))

    def test_users_and_current_rows_persist_in_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "datasets.sqlite3"
            repository = DatasetRepository(path)
            self.assertTrue(repository.create_user("new-user", hash_password("secret")))
            repository.append_current([
                {"id": "APP-1", "row_number": 1, "data": {"GENDER": "M"}}
            ])

            reopened = DatasetRepository(path)
            self.assertEqual(reopened.get_user("new-user")["username"], "new-user")
            self.assertEqual(reopened.get_current_applicants()[0]["id"], "APP-1")

    def test_issue_token_uses_registered_user(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = DatasetRepository(Path(directory) / "datasets.sqlite3")
            repository.create_user("registered", hash_password("secret"))

            token = issue_token(LoginRequest(username="registered", password="secret"), repository)

            self.assertEqual(token["token_type"], "bearer")
            self.assertTrue(token["access_token"])


    def test_application_review_tables_are_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "datasets.sqlite3"
            repository = DatasetRepository(path)
            repository.create_user("analyst", hash_password("secret"), role="analyst")
            repository.create_user("reviewer", hash_password("secret"), role="reviewer")

            application_id = repository.create_application(
                "analyst",
                "manual",
                {"GENDER": "M", "TOTAL_INCOME": 50000, "CREDIT_AMOUNT": 100000},
                source_applicant_id="APP-42",
            )
            repository.ensure_model_version("model-v1", {"model_source": "test"})
            prediction_id = repository.create_prediction(
                application_id,
                "model-v1",
                {
                    "default_probability": 0.13,
                    "risk_score": 13.0,
                    "trust_score": 87.0,
                    "confidence": 0.87,
                    "risk_tier": "Tier B",
                    "decision": "Manual Review Required",
                    "approve_threshold": 0.08,
                    "decline_threshold": 0.20,
                    "recommended_rate": "10.5%",
                    "loan_amount_decision": "Approve Requested Amount",
                    "input_snapshot": {"GENDER": "M"},
                    "processed_feature_snapshot": {"GENDER": "M"},
                },
            )
            review_id = repository.create_review(
                application_id,
                prediction_id,
                "reviewer",
                "approved",
                "Approved after review.",
                is_override=False,
            )

            self.assertEqual(repository.get_user("reviewer")["role"], "reviewer")
            self.assertEqual(repository.list_predictions(application_id)[0]["prediction_id"], prediction_id)
            self.assertEqual(repository.list_reviews(application_id)[0]["review_id"], review_id)
            self.assertEqual(repository.get_application(application_id, "analyst")["source_applicant_id"], "APP-42")


if __name__ == "__main__":
    unittest.main()
