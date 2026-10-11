"""Phase 3C-1 security contracts, verified only against disposable SQLite."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import jwt
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from Backend import main
from Backend.auth import hash_password
from Backend.config import JWT_SECRET
from Backend.repository import AssignmentError, DatasetRepository


@pytest.fixture
def setup(tmp_path, monkeypatch):
    repo = DatasetRepository(tmp_path / "security.sqlite3")
    hashed = hash_password("test-secret")
    for username, role in (("admin", "admin"), ("second", "admin"),
                           ("analyst", "analyst"), ("reviewer", "reviewer"), ("replacement", "reviewer")):
        repo.create_user(username, hashed, role)
    monkeypatch.setattr(main, "datasets", repo)
    with TestClient(main.app) as client:
        def login(username):
            response = client.post("/auth/login", json={"username": username, "password": "test-secret"})
            assert response.status_code == 200
            return {"Authorization": "Bearer " + response.json()["access_token"]}
        yield repo, client, login


def change(client, headers, username, field="status", **values):
    return client.patch(f"/api/admin/users/{username}/{field}", headers=headers,
                        json={"reason": "Security test", "expected_version": 0, **values})


def test_directory_admin_only_pagination_and_no_secrets(setup):
    repo, client, login = setup
    for username in ("analyst", "reviewer"):
        assert client.get("/api/admin/users", headers=login(username)).status_code == 403
        assert change(client, login(username), "replacement", is_active=False).status_code == 403
        assert change(client, login(username), "replacement", "role", role="analyst").status_code == 403
        assert client.get("/api/admin/audit", headers=login(username)).status_code == 403
    response = client.get("/api/admin/users?limit=2&offset=1", headers=login("admin"))
    assert response.status_code == 200
    assert response.json()["total"] == 5
    assert len(response.json()["users"]) == 2
    assert set(response.json()["users"][0]) == {"username", "role", "is_active", "version", "created_at"}
    for params in ("limit=101", "limit=0", "offset=-1"):
        assert client.get("/api/admin/users?" + params, headers=login("admin")).status_code == 422


@pytest.mark.parametrize("deactivate", [True, False])
def test_deactivation_or_old_version_rejects_every_protected_route(setup, deactivate):
    repo, client, login = setup
    headers = login("analyst")
    response = (change(client, login("admin"), "analyst", is_active=False) if deactivate else
                change(client, login("admin"), "analyst", "role", role="reviewer"))
    assert response.status_code == 200
    assert client.post("/auth/login", json={"username": "analyst", "password": "test-secret"}).status_code == (401 if deactivate else 200)
    for route in main.app.routes:
        if not isinstance(route, APIRoute) or route.path in {"/", "/health", "/auth/login", "/auth/register", "/auth/demo", "/auth/demo/{role}"}:
            continue
        path = route.path
        for param in route.param_convertors:
            path = path.replace("{" + param + "}", "fixture")
        for method in route.methods:
            response = client.request(method, path, headers=headers, json={})
            assert response.status_code == 401, (method, path, response.text)


def test_versions_reactivation_role_claims_and_missing_version(setup):
    repo, client, login = setup
    admin = login("admin")
    old = login("analyst")
    assert change(client, admin, "analyst", "role", role="reviewer").json()["version"] == 1
    assert client.get("/auth/me", headers=old).status_code == 401
    current = login("analyst")
    assert client.get("/api/default-data", headers=current).status_code == 403
    assert change(client, admin, "analyst", is_active=False, expected_version=1).status_code == 200
    assert change(client, admin, "analyst", is_active=True, expected_version=2).json()["version"] == 3
    assert client.get("/auth/me", headers=current).status_code == 401
    assert client.get("/auth/me", headers=login("analyst")).status_code == 200
    token = jwt.encode({"sub": "analyst", "role": "admin", "version": 3,
                        "exp": datetime.now(timezone.utc).timestamp() + 60}, JWT_SECRET, algorithm="HS256")
    assert client.get("/api/admin/users", headers={"Authorization": "Bearer " + token}).status_code == 403
    token = jwt.encode({"sub": "admin", "exp": datetime.now(timezone.utc).timestamp() + 60}, JWT_SECRET, algorithm="HS256")
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401


def test_registration_invalid_inputs_and_self_lockout(setup):
    repo, client, login = setup
    admin = login("admin")
    for role in ("admin", "reviewer"):
        response = client.post("/auth/register", json={"username": "escalate", "password": "secret", "role": role})
        assert response.status_code == 422
        assert "secret" not in response.text
    response = change(client, admin, "analyst", is_active=False, password_hash="confidential-hash")
    assert response.status_code == 422 and "confidential-hash" not in response.text
    assert repo.get_user("escalate") is None
    for values in ({"is_active": False}, {"role": "analyst"}):
        assert change(client, admin, "admin", "role" if "role" in values else "status", **values).status_code == 409
    for values in ({"is_active": False, "expected_version": -1}, {"is_active": False, "expected_version": True},
                   {"is_active": False, "reason": " "}, {"is_active": "false"}):
        assert change(client, admin, "analyst", **values).status_code == 422
    assert change(client, admin, "analyst", "role", role="superadmin").status_code == 422
    assert change(client, admin, "analyst", expected_version=12, is_active=False).status_code == 409
    assert client.delete("/api/admin/users/analyst", headers=admin).status_code == 404


def test_pending_reviewer_and_active_assignment_enforcement(setup):
    repo, client, login = setup
    admin = login("admin")
    application = repo.create_application("analyst", "manual", {}, assigned_reviewer="reviewer")
    for field, values in (("status", {"is_active": False}), ("role", {"role": "analyst"})):
        assert change(client, admin, "reviewer", field, **values).status_code == 409
    before = repo.get_application(application)
    repo.reassign_application(application, "admin", "replacement", "Reallocate", before["updated_at"], actor_version=0)
    assert change(client, admin, "reviewer", is_active=False).status_code == 200
    assert "reviewer" not in client.get("/api/reviewers", headers=login("analyst")).json()["reviewers"]
    assert "reviewer" not in {row["username"] for row in
                              client.get("/api/admin/assignments", headers=admin).json()["reviewer_workload"]}
    with pytest.raises(AssignmentError):
        repo.create_application("analyst", "manual", {}, assigned_reviewer="reviewer")
    with pytest.raises(AssignmentError):
        repo.reassign_application(application, "admin", "reviewer", "Try inactive", repo.get_application(application)["updated_at"])
    with pytest.raises(AssignmentError):
        repo.create_application("analyst", "manual", {}, assigned_reviewer="admin")


def test_account_audit_rollback_and_repeatable_initialization(setup):
    repo, client, login = setup
    with repo._connection() as connection:
        connection.execute("CREATE TRIGGER fail_user_audit BEFORE INSERT ON user_audit_logs BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
    with pytest.raises(sqlite3.IntegrityError):
        repo.change_user("admin", 0, "analyst", 0, "Test", is_active=False)
    assert repo.get_user("analyst")["is_active"] == 1
    assert repo.get_user("analyst")["version"] == 0
    assert repo.list_admin_audit()["total"] == 0
    with repo._connection() as connection:
        connection.execute("DROP TRIGGER fail_user_audit")
    repo.change_user("admin", 0, "analyst", 0, "Test", is_active=False)
    reopened = DatasetRepository(repo.path)
    assert reopened.get_user("analyst")["version"] == 1
    assert reopened.list_admin_audit()["total"] == 1


def test_review_audit_failure_rolls_back_all_three_writes(setup):
    repo, client, login = setup
    application = repo.create_application("analyst", "manual", {}, assigned_reviewer="reviewer")
    repo.ensure_model_version("fixture", {})
    prediction = repo.persist_prediction_assessment(application, "fixture", {
        "default_probability": .1, "risk_score": 10, "trust_score": 90, "confidence": .9,
        "decision": "Auto Approve"}, {"feature_contributions": []})
    before = repo.get_application(application)
    with repo._connection() as connection:
        connection.execute("CREATE TRIGGER fail_review_audit BEFORE INSERT ON audit_logs BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
    with pytest.raises(sqlite3.IntegrityError):
        repo.finalize_review(application, prediction, "reviewer", "approved", "", None, False, actor_version=0)
    assert repo.list_reviews(application) == []
    assert repo.get_application(application) == before
    assert repo.list_audit_logs(application) == []


def test_global_and_application_audit_sanitization_filters(setup):
    repo, client, login = setup
    application = repo.create_application("analyst", "manual", {})
    repo.add_audit_log(application, "analyst", "application_created", None,
                       {"source_type": "manual", "applicant_snapshot": {"TOTAL_INCOME": 50000},
                        "password_hash": "secret", "dataset_id": {"token": "secret"}})
    repo.add_audit_log(application, "analyst", "unknown", {"token": "secret"}, {"financial": 1})
    repo.change_user("admin", 0, "reviewer", 0, "Staff change", is_active=False)
    admin = login("admin")
    response = client.get("/api/admin/audit?limit=2", headers=admin)
    assert response.json()["total"] == 3 and len(response.json()["events"]) == 2
    for params, total in (({"actor": "analyst"}, 2), ({"application_id": application}, 2),
                          ({"event_type": "user_deactivated"}, 1), ({"date_from": "2099-01-01"}, 0),
                          ({"date_to": "2000-01-01"}, 0)):
        assert client.get("/api/admin/audit", headers=admin, params=params).json()["total"] == total
    assert client.get("/api/admin/audit?date_from=2099-01-01&date_to=2000-01-01", headers=admin).status_code == 422
    for url, headers in (("/api/admin/audit?limit=100", admin),
                         (f"/api/applications/{application}/audit", login("analyst"))):
        body = client.get(url, headers=headers).text
        for forbidden in ("secret", "TOTAL_INCOME", "password_hash", "applicant_snapshot", '"token"'):
            assert forbidden not in body
    assert client.patch("/api/admin/audit", headers=admin, json={}).status_code == 405


def test_concurrent_admin_changes_cannot_remove_both_admins(setup):
    repo, client, login = setup
    def demote(actor, target):
        try:
            repo.change_user(actor, 0, target, 0, "Concurrent change", role="analyst")
            return 200
        except AssignmentError as exc:
            return exc.status_code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda pair: demote(*pair), [("admin", "second"), ("second", "admin")]))
    assert sorted(results) == [200, 401]
    assert sum(user["role"] == "admin" and user["is_active"] for user in repo.list_users()["users"]) == 1


def test_concurrent_assignment_and_deactivation_preserve_eligibility(setup):
    repo, client, login = setup
    application = repo.create_application("analyst", "manual", {}, assigned_reviewer="reviewer")
    stamp = repo.get_application(application)["updated_at"]
    def assign():
        try:
            repo.reassign_application(application, "admin", "replacement", "Balance", stamp, actor_version=0)
        except AssignmentError:
            pass
    def disable():
        try:
            repo.change_user("admin", 0, "replacement", 0, "Disable", is_active=False)
        except AssignmentError:
            pass
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(assign), pool.submit(disable)]
        for future in futures:
            future.result()
    assigned = repo.get_application(application)["assigned_reviewer"]
    assert repo.get_user(assigned)["is_active"]


def test_write_authorization_rechecked_after_session_change(setup):
    repo, client, login = setup
    repo.change_user("admin", 0, "second", 0, "Demote", role="analyst")
    with pytest.raises(AssignmentError) as error:
        repo.change_user("second", 0, "reviewer", 0, "Stale admin", is_active=False)
    assert error.value.status_code == 401
    assert repo.get_user("reviewer")["is_active"]


def test_concurrent_compare_and_swap_changes_only_once(setup):
    repo, client, login = setup
    def update(role):
        try:
            repo.change_user("admin", 0, "analyst", 0, "Concurrent edit", role=role)
            return 200
        except AssignmentError as exc:
            return exc.status_code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(update, ["reviewer", "admin"]))
    assert sorted(results) == [200, 409]
    assert repo.get_user("analyst")["version"] == 1
    assert repo.list_admin_audit()["total"] == 1


def test_concurrent_initial_assignment_and_deactivation(setup):
    repo, client, login = setup
    def create():
        try:
            repo.create_application("analyst", "manual", {}, assigned_reviewer="replacement", actor_version=0)
        except AssignmentError:
            pass
    def disable():
        try:
            repo.change_user("admin", 0, "replacement", 0, "Disable", is_active=False)
        except AssignmentError:
            pass
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(create), pool.submit(disable)]
        for future in futures:
            future.result()
    for application in repo.list_applications():
        assert repo.get_user(application["assigned_reviewer"])["is_active"]


def test_demo_bootstrap_fresh_only_and_no_hardcoded_password(tmp_path, monkeypatch):
    from scripts import seed_dev_users
    path = str(tmp_path / "demo.sqlite3")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SEED_DEV_USERS_CONFIRM", raising=False)
    monkeypatch.setattr("sys.argv", ["seed_dev_users", "--db", path, "--confirm", path])
    monkeypatch.setattr(seed_dev_users.getpass, "getpass", lambda _: "chosen-secret")
    assert seed_dev_users.main() == 0
    repo = DatasetRepository(tmp_path / "demo.sqlite3")
    assert repo.get_user("admin")["role"] == "admin"
    before = (tmp_path / "demo.sqlite3").read_bytes()
    assert seed_dev_users.main() == 2
    assert (tmp_path / "demo.sqlite3").read_bytes() == before
    monkeypatch.setenv("APP_ENV", "production")
    assert seed_dev_users.main() == 2
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql://never-connect")
    assert seed_dev_users.main() == 2


def test_additive_initialization_preserves_legacy_users(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, created_at TEXT NOT NULL, role TEXT NOT NULL)")
        connection.execute("INSERT INTO users VALUES ('existing','kept-hash','2026-01-01','admin')")
    for _ in range(2):
        repo = DatasetRepository(path)
        user = repo.get_user("existing")
        assert user["password_hash"] == "kept-hash"
        assert user["is_active"] == 1 and user["version"] == 0
