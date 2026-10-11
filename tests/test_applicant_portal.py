"""Phase 4 isolation, validation, lifecycle and concurrency checks on disposable data."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute

from Backend import main
from Backend.auth import hash_password
from Backend.repository import AssignmentError, DatasetRepository

INPUT = {"GENDER": "M", "QUALIFICATION": "Higher education", "FAMILY_STATUS": "Single / not married",
         "OCCUPATION": "Core staff", "CONTRACT_TYPE": "Cash loans", "TOTAL_INCOME": 50000,
         "CREDIT_AMOUNT": 150000, "ANNUAL_LOAN_PAYMENT": 12000, "GOODS_PRICE": 150000,
         "AGE": 30, "YEARS_OF_EXPERIENCE": 5, "CREDIT_SCORE": 710, "CREDIT_HISTORY": 0}


@pytest.fixture(params=["sqlite"] + (["postgres"] if os.environ.get("CREDIT_PHASE4_TEST_DATABASE_URL") else []))
def portal(tmp_path, monkeypatch, request):
    database_url = None
    schema = None
    if request.param == "postgres":
        import psycopg
        from psycopg.conninfo import make_conninfo, conninfo_to_dict
        from psycopg import sql
        base_url = os.environ["CREDIT_PHASE4_TEST_DATABASE_URL"]
        assert conninfo_to_dict(base_url).get("host") in {"127.0.0.1", "localhost"}, "Phase 4 PostgreSQL tests require a disposable localhost server"
        schema = "phase4_" + uuid4().hex
        with psycopg.connect(base_url) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        database_url = make_conninfo(base_url, options="-c search_path=" + schema)
    repo = DatasetRepository(tmp_path / "portal.sqlite3", database_url=database_url)
    monkeypatch.setattr(main, "datasets", repo)
    client = TestClient(main.app)
    for username, role in (("analyst", "analyst"), ("second", "analyst"), ("reviewer", "reviewer"), ("admin", "admin")):
        repo.create_user(username, hash_password("fictional-test-password"), role)

    def headers(username, register=False):
        response = client.post("/auth/register" if register else "/auth/login",
                               json={"username": username, "password": "fictional-test-password"})
        assert response.status_code == 200
        return {"Authorization": "Bearer " + response.json()["access_token"]}

    yield repo, client, headers
    client.close()
    if schema:
        with psycopg.connect(base_url) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def submit(client, headers):
    response = client.post("/api/applicant/applications", headers=headers, json={"applicant_snapshot": INPUT})
    assert response.status_code == 201, response.text
    return response.json()


def test_registration_and_staff_login(portal):
    repo, client, headers = portal
    applicant = headers("applicant", True)
    assert client.get("/auth/me", headers=applicant).json()["role"] == "applicant"
    for role in ("analyst", "reviewer", "admin"):
        assert client.get("/auth/me", headers=headers(role)).json()["role"] == role
    for field in ("role", "is_active", "version", "password_hash"):
        response = client.post("/auth/register", json={"username": "attack", "password": "secret", field: "admin"})
        assert response.status_code == 422
    assert repo.get_user("attack") is None


def test_submission_isolation_and_no_inference(portal):
    repo, client, headers = portal
    a, b = headers("alice", True), headers("bob", True)
    with patch.object(main, "process_application", side_effect=AssertionError("submission invoked inference")):
        application = submit(client, a)
    app_id = application["application_id"]
    safe = client.get(f"/api/applicant/applications/{app_id}", headers=a).json()
    assert safe["applicant_snapshot"] == INPUT
    assert set(safe) == {"application_id", "status", "progress", "loan_amount", "created_at", "updated_at", "applicant_snapshot"}
    assert client.get(f"/api/applicant/applications/{app_id}", headers=b).status_code == 404
    assert client.get("/api/applicant/applications", headers=b).json()["total"] == 0
    assert client.get(f"/api/applicant/applications/{app_id}").status_code == 401
    event = repo.list_audit_logs(app_id)[0]
    assert event["new_value"] == {"source_type": "applicant"}
    assert not repo.list_predictions(app_id)


@pytest.mark.parametrize("field,value", [("APPROVE_THRESHOLD", .99), ("DECLINE_THRESHOLD", .01),
    ("owner", "admin"), ("assigned_reviewer", "reviewer"), ("status", "approved"), ("source_type", "manual"),
    ("TOTAL_INCOME", -1), ("AGE", 17), ("YEARS_OF_EXPERIENCE", 81), ("CREDIT_SCORE", 900), ("TOTAL_INCOME", "Infinity")])
def test_invalid_applicant_inputs(portal, field, value):
    _, client, headers = portal
    response = client.post("/api/applicant/applications", headers=headers("alice", True),
                           json={"applicant_snapshot": dict(INPUT, **{field: value})})
    assert response.status_code == 422


def test_top_level_fields_and_pagination(portal):
    _, client, headers = portal
    a = headers("alice", True)
    for field in ("owner", "role", "status", "source_type", "assigned_analyst", "reviewer_username"):
        assert client.post("/api/applicant/applications", headers=a, json={"applicant_snapshot": INPUT, field: "admin"}).status_code == 422
    for _ in range(3):
        submit(client, a)
    page = client.get("/api/applicant/applications?limit=2&offset=2", headers=a).json()
    assert page["total"] == 3 and len(page["applications"]) == 1
    assert "applicant_snapshot" not in page["applications"][0]
    assert client.get("/api/applicant/applications?limit=101", headers=a).status_code == 422


def test_all_internal_routes_deny_applicants(portal):
    _, client, headers = portal
    a = headers("alice", True)
    app_id = submit(client, a)["application_id"]
    for route in main.app.routes:
        if not isinstance(route, APIRoute) or route.path in {"/", "/health", "/auth/login", "/auth/register", "/auth/me", "/auth/demo", "/auth/demo/{role}"} or route.path.startswith("/api/applicant/"):
            continue
        path = route.path
        for param in route.param_convertors:
            path = path.replace("{" + param + "}", app_id if param == "application_id" else "fixture")
        for method in route.methods:
            # Dependency authorization precedes body validation for existing malformed payloads.
            response = client.request(method, path, headers=a, json={})
            assert response.status_code == 403, (method, path, response.text)


def test_repeatable_initialization(portal):
    repo, client, headers = portal
    app_id = submit(client, headers("alice", True))["application_id"]
    DatasetRepository(repo.path, database_url=repo.database_url)
    DatasetRepository(repo.path, database_url=repo.database_url)
    assert repo.get_application(app_id)["owner"] == "alice"
    with repo._connection() as connection:
        assert "assigned_analyst" in repo._table_columns(connection, "loan_applications")
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE migration_name='add_applicant_intake'").fetchone()[0] == 1


def claim(client, headers, application):
    return client.post(f"/api/analyst/intake/{application['application_id']}/claim", headers=headers,
                       json={"reviewer_username": "reviewer", "expected_updated_at": application["updated_at"]})


def test_intake_claim_pagination_and_concurrent_winner(portal):
    repo, client, headers = portal
    a = headers("alice", True)
    applications = [submit(client, a) for _ in range(3)]
    analyst, second = headers("analyst"), headers("second")
    page = client.get("/api/analyst/intake?limit=2&offset=2", headers=analyst).json()
    assert page["total"] == 3 and len(page["applications"]) == 1
    assert "applicant_snapshot" not in page["applications"][0]
    for role in ("admin", "reviewer"):
        assert client.get("/api/analyst/intake", headers=headers(role)).status_code == 403
        assert claim(client, headers(role), applications[0]).status_code == 403
    assert client.get(f"/api/applications/{applications[0]['application_id']}", headers=analyst).status_code == 404
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda actor: claim(client, actor, applications[0]), (analyst, second)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    saved = repo.get_application(applications[0]["application_id"])
    assert saved["owner"] == "alice" and saved["assigned_reviewer"] == "reviewer"
    assert client.get("/api/analyst/intake", headers=analyst).json()["total"] == 2
    assert sum(event["event_type"] == "analyst_claimed" for event in repo.list_audit_logs(saved["application_id"])) == 1


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_complete_four_role_workflow_and_terminal_status(portal, decision):
    repo, client, headers = portal
    a = headers("alice", True)
    analyst, reviewer, admin = headers("analyst"), headers("reviewer"), headers("admin")
    application = submit(client, a)
    app_id = application["application_id"]
    assert claim(client, analyst, application).status_code == 200
    assert client.get("/api/reviewers/pending", headers=reviewer).json()["total"] == 0
    assert client.get(f"/api/applications/{app_id}", headers=headers("second")).status_code == 404
    assessment = client.post(f"/api/applications/{app_id}/assess", headers=analyst)
    assert assessment.status_code == 200, assessment.text
    assert client.get("/api/reviewers/pending", headers=reviewer).json()["applications"][0]["application_id"] == app_id
    prediction = assessment.json()["prediction_id"]
    assert client.get(f"/api/applications/{app_id}/predictions/{prediction}/shap", headers=reviewer).json()["explanation"]["feature_contributions"]
    reviewed = client.post(f"/api/applications/{app_id}/reviews", headers=reviewer,
        json={"prediction_id": prediction, "reviewer_decision": decision, "override_reason": "Fictional acceptance rationale", "review_notes": "INTERNAL ONLY"})
    assert reviewed.status_code == 200
    with patch.object(main, "process_application", side_effect=AssertionError("final case invoked inference")):
        assert client.post(f"/api/applications/{app_id}/assess", headers=analyst).status_code == 409
    detail = client.get(f"/api/applicant/applications/{app_id}", headers=a)
    assert detail.json()["progress"] == decision.capitalize()
    assert "INTERNAL ONLY" not in detail.text and "prediction" not in detail.text and "reviewer" not in detail.text
    assert client.get("/api/admin/overview", headers=admin).json()["totals"]["total"] == 1
    events = client.get("/api/admin/audit", headers=admin).json()["events"]
    assert {event["event_type"] for event in events} >= {"application_created", "analyst_claimed", "prediction_generated", "manual_review"}
    claim_event = next(event for event in events if event["event_type"] == "analyst_claimed")
    assert claim_event["new_value"]["assigned_analyst"] == "analyst"
    assert repo.get_application(app_id)["owner"] == "alice"


def test_claimed_analyst_safeguards_and_applicant_revocation(portal):
    _, client, headers = portal
    a, analyst, admin = headers("alice", True), headers("analyst"), headers("admin")
    assert claim(client, analyst, submit(client, a)).status_code == 200
    for kind, values in (("status", {"is_active": False}), ("role", {"role": "reviewer"})):
        response = client.patch(f"/api/admin/users/analyst/{kind}", headers=admin,
            json=dict(values, expected_version=0, reason="Fictional account change"))
        assert response.status_code == 409
    assert client.patch("/api/admin/users/alice/role", headers=admin,
        json={"role": "admin", "expected_version": 0, "reason": "Attempted conversion"}).status_code == 422
    assert client.patch("/api/admin/users/alice/status", headers=admin,
        json={"is_active": False, "expected_version": 0, "reason": "Test revocation"}).status_code == 200
    assert client.get("/api/applicant/applications", headers=a).status_code == 401


def test_review_wins_against_inflight_assessment(portal):
    repo, client, headers = portal
    a, analyst, reviewer = headers("alice", True), headers("analyst"), headers("reviewer")
    application = submit(client, a)
    assert claim(client, analyst, application).status_code == 200
    app_id = application["application_id"]
    assert client.post(f"/api/applications/{app_id}/assess", headers=analyst).status_code == 200
    entered, release = Event(), Event()
    original = main.process_application

    def paused(snapshot):
        entered.set()
        assert release.wait(15)
        return original(snapshot)

    with patch.object(main, "process_application", side_effect=paused), ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, f"/api/applications/{app_id}/assess", headers=analyst)
        assert entered.wait(15)
        try:
            result = client.post(f"/api/applications/{app_id}/reviews", headers=reviewer,
                json={"reviewer_decision": "approved", "override_reason": "Concurrency verification"})
            assert result.status_code == 200
        finally:
            release.set()
        assert pending.result(timeout=20).status_code == 409
    assert repo.get_application(app_id)["status"] == "approved"
    assert len(repo.list_predictions(app_id)) == 1


def test_claim_rejects_stale_token_and_ineligible_reviewer(portal):
    repo, client, headers = portal
    a, analyst = headers("alice", True), headers("analyst")
    application = submit(client, a)
    assert claim(client, analyst, dict(application, updated_at="stale")).status_code == 409
    response = client.post(f"/api/analyst/intake/{application['application_id']}/claim", headers=analyst,
                           json={"reviewer_username": "second", "expected_updated_at": application["updated_at"]})
    assert response.status_code == 422
    repo.set_user_role("analyst", "reviewer")
    assert claim(client, analyst, application).status_code == 401


def test_reviewer_reassignment_after_claim(portal):
    repo, client, headers = portal
    a, analyst, admin = headers("alice", True), headers("analyst"), headers("admin")
    application = submit(client, a)
    app_id = application["application_id"]
    assert claim(client, analyst, application).status_code == 200
    repo.create_user("replacement", hash_password("fictional-test-password"), "reviewer")
    current = repo.get_application(app_id)
    changed = client.patch(f"/api/admin/applications/{app_id}/assignment", headers=admin,
        json={"reviewer_username": "replacement", "expected_updated_at": current["updated_at"], "reason": "Fictional workload transfer"})
    assert changed.status_code == 200
    assert client.get(f"/api/applications/{app_id}", headers=headers("reviewer")).status_code == 404
    assert client.post(f"/api/applications/{app_id}/assess", headers=analyst).status_code == 200
    assert client.get("/api/reviewers/pending", headers=headers("replacement")).json()["total"] == 1


def test_stale_assessment_and_revoked_actor_roll_back(portal):
    repo, client, headers = portal
    a, analyst = headers("alice", True), headers("analyst")
    application = submit(client, a)
    assert claim(client, analyst, application).status_code == 200
    app_id = application["application_id"]
    # A stale concurrent assessment must fail before inserting any prediction/explanation.
    repo.ensure_model_version("fixture", {})
    with pytest.raises(AssignmentError) as error:
        repo.persist_prediction_assessment(app_id, "fixture", {}, {}, actor="analyst", actor_version=0,
                                           expected_updated_at=application["updated_at"])
    assert error.value.status_code == 409 and not repo.list_predictions(app_id)
    repo.update_user_credentials("analyst", hash_password("new-fictional-password"))
    with pytest.raises(AssignmentError) as error:
        repo.persist_prediction_assessment(app_id, "fixture", {}, {}, actor="analyst", actor_version=0,
                                           expected_updated_at=repo.get_application(app_id)["updated_at"])
    assert error.value.status_code == 401 and not repo.list_predictions(app_id)


def test_assessment_wins_rejecting_a_stale_review(portal):
    repo, client, headers = portal
    application = submit(client, headers("alice", True))
    analyst = headers("analyst")
    assert claim(client, analyst, application).status_code == 200
    app_id = application["application_id"]
    first = client.post(f"/api/applications/{app_id}/assess", headers=analyst).json()
    observed = repo.get_application(app_id)
    assert client.post(f"/api/applications/{app_id}/assess", headers=analyst).status_code == 200
    with pytest.raises(ValueError):
        repo.finalize_review(app_id, first["prediction_id"], "reviewer", "approved", "", "Fictional override", True,
                             expected_updated_at=observed["updated_at"], actor_version=0)
    assert repo.get_application(app_id)["status"] == "pending_review" and not repo.list_reviews(app_id)


def test_postgres_fresh_connection_after_disconnect(portal):
    repo, _, _ = portal
    if not repo.is_postgres:
        pytest.skip("PostgreSQL-only forced-disconnect check")
    import psycopg
    with pytest.raises(psycopg.Error):
        with repo._connection() as connection:
            pid = connection.execute("SELECT pg_backend_pid()").fetchone()[0]
            with psycopg.connect(repo.database_url) as controller:
                assert controller.execute("SELECT pg_terminate_backend(%s)", (pid,)).fetchone()[0]
            connection.execute("SELECT 1")
    # The next request opens a fresh connection; no unsafe write retry or retained idle socket.
    assert repo.get_user("analyst")["role"] == "analyst"


def test_atomic_applicant_creation_audit_failure(portal):
    repo, client, headers = portal
    a = headers("alice", True)
    with repo._connection() as connection:
        connection.execute("ALTER TABLE audit_logs RENAME TO unavailable_audit")
    with pytest.raises(Exception):
        submit(client, a)
    assert repo.application_page(role="applicant", username="alice")["total"] == 0


def test_legacy_application_migration_preserves_data(tmp_path):
    import sqlite3
    path = tmp_path / "legacy-applications.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE loan_applications (application_id TEXT PRIMARY KEY, owner TEXT NOT NULL,
            assigned_reviewer TEXT, source_type TEXT NOT NULL, dataset_id TEXT, source_applicant_id TEXT,
            applicant_snapshot TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        connection.execute("INSERT INTO loan_applications VALUES ('legacy','historical-analyst',NULL,'manual',NULL,NULL,'{}','submitted','2026-01-01','2026-01-01')")
    for _ in range(2):
        repo = DatasetRepository(path)
        saved = repo.get_application("legacy")
        assert saved["owner"] == "historical-analyst" and saved["assigned_analyst"] is None


def test_production_requires_postgres_and_explicit_configuration(monkeypatch):
    import runpy
    monkeypatch.setenv("APP_ENV", "production")
    for key in ("CREDIT_JWT_SECRET", "CREDIT_AUTH_USERNAME", "CREDIT_AUTH_PASSWORD"):
        monkeypatch.setenv(key, "fictional-test-configuration")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        runpy.run_path(str(Path(main.backend_dir) / "config.py"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///ephemeral-file")
    with pytest.raises(RuntimeError, match="must use PostgreSQL"):
        runpy.run_path(str(Path(main.backend_dir) / "config.py"))
    # Configuration inspection only: this URL is never opened.
    monkeypatch.setenv("DATABASE_URL", "postgres://local-config-check/unused")
    settings = runpy.run_path(str(Path(main.backend_dir) / "config.py"))
    assert settings["DATABASE_URL"] == "postgresql://local-config-check/unused"
