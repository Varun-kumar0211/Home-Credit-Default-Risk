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


if __name__ == "__main__":
    unittest.main()
