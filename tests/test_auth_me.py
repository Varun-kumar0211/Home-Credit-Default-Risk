import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt

from fastapi.testclient import TestClient

from Backend import main
from Backend.auth import hash_password
from Backend.config import JWT_SECRET
from Backend.repository import DatasetRepository


class AuthMeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repository = DatasetRepository(Path(self.directory.name) / "auth-me.sqlite3")
        self.previous_repository = main.datasets
        main.datasets = self.repository
        self.client = TestClient(main.app)

    def tearDown(self):
        main.datasets = self.previous_repository
        self.directory.cleanup()

    def _register(self, username, password="secret"):
        response = self.client.post("/auth/register", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200)
        return response.json()["access_token"]

    def _token(self, username, role="analyst", expired=False):
        now = datetime.now(timezone.utc)
        payload = {
            "sub": username,
            "version": 0,
            "role": role,
            "iat": now - timedelta(hours=9) if expired else now,
            "exp": now - timedelta(hours=1) if expired else now + timedelta(hours=8),
        }
        return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

    def test_me_returns_current_role_for_each_role(self):
        self.repository.create_user("me-analyst", hash_password("secret"), role="analyst")
        analyst_token = self.client.post("/auth/login", json={"username": "me-analyst", "password": "secret"}).json()["access_token"]
        self.repository.create_user("me-reviewer", hash_password("secret"), role="reviewer")
        self.repository.create_user("me-admin", hash_password("secret"), role="admin")
        reviewer_login = self.client.post("/auth/login", json={"username": "me-reviewer", "password": "secret"})
        admin_login = self.client.post("/auth/login", json={"username": "me-admin", "password": "secret"})

        cases = [
            (analyst_token, "me-analyst", "analyst"),
            (reviewer_login.json()["access_token"], "me-reviewer", "reviewer"),
            (admin_login.json()["access_token"], "me-admin", "admin"),
        ]
        for token, username, role in cases:
            response = self.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"username": username, "role": role})
            self.assertNotIn("password_hash", response.json())

    def test_me_rejects_missing_and_invalid_tokens(self):
        self.assertEqual(self.client.get("/auth/me").status_code, 401)
        self.assertEqual(
            self.client.get("/auth/me", headers={"Authorization": "Bearer invalid"}).status_code, 401
        )

    def test_me_rejects_expired_and_tampered_tokens(self):
        token = self._register("me-expiry")
        self.assertEqual(
            self.client.get("/auth/me", headers={"Authorization": f"Bearer {self._token('me-expiry', expired=True)}"}).status_code,
            401,
        )
        tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
        self.assertEqual(
            self.client.get("/auth/me", headers={"Authorization": f"Bearer {tampered}"}).status_code, 401
        )

    def test_me_prefers_database_role_over_stale_claim(self):
        self._register("me-stale")
        stale = self._token("me-stale", role="admin")
        response = self.client.get("/auth/me", headers={"Authorization": f"Bearer {stale}"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["role"], "applicant")

    def test_me_rejects_deleted_account(self):
        token = self._register("me-deleted")
        with self.repository._connection() as connection:
            connection.execute("DELETE FROM users WHERE username=?", ("me-deleted",))
        self.assertEqual(
            self.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code, 401
        )

    def test_existing_login_and_register_still_work(self):
        token = self._register("me-legacy")
        login = self.client.post("/auth/login", json={"username": "me-legacy", "password": "secret"})
        self.assertEqual(login.status_code, 200)
        me_response = self.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me_response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
