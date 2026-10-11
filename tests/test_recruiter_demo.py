"""Small deployment regression checks; only disposable test databases."""
import io
import json
import os
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from Backend import main
from Backend.auth import hash_password
from Backend.demo import DEMO_ACCOUNTS, initialize_demo
from Backend.repository import DatasetRepository


@pytest.fixture(params=["sqlite"] + (["postgres"] if os.environ.get("CREDIT_PHASE4_TEST_DATABASE_URL") else []))
def demo(tmp_path, monkeypatch, request):
    url = None
    schema = None
    if request.param == "postgres":
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo, conninfo_to_dict
        base = os.environ["CREDIT_PHASE4_TEST_DATABASE_URL"]
        assert conninfo_to_dict(base).get("host") in {"localhost", "127.0.0.1"}
        schema = "demo_test_" + uuid4().hex
        with psycopg.connect(base) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        url = make_conninfo(base, options="-c search_path=" + schema)
    repo = DatasetRepository(tmp_path / "demo.sqlite3", database_url=url)
    monkeypatch.setattr(main, "datasets", repo)
    monkeypatch.setattr(main, "DEMO_ENABLED", True)
    initialize_demo(repo, hash_password, main.model_id, main.artifact_hash)
    with TestClient(main.app) as client:
        yield repo, client
    if schema:
        with psycopg.connect(base) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def demo_headers(client, role):
    response = client.post("/auth/demo/" + role)
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def test_demo_roles_are_read_only_and_cannot_manage_accounts(demo):
    repo, client = demo
    for role in DEMO_ACCOUNTS:
        headers = demo_headers(client, role)
        assert client.get("/auth/me", headers=headers).json()["read_only"] is True
        response = client.patch("/api/admin/users/" + DEMO_ACCOUNTS["analyst"] + "/role", headers=headers,
                                json={"role": "admin", "expected_version": 0, "reason": "Attempt escalation"})
        assert response.status_code == 403
        assert client.post("/api/applications/anything/assess", headers=headers).status_code == 403
    applicant = demo_headers(client, "applicant")
    assert client.get("/api/applicant/applications", headers=applicant).json()["total"] == 2
    assert client.get("/api/admin/overview", headers=applicant).status_code == 403
    admin = demo_headers(client, "admin")
    assert client.get("/api/admin/overview", headers=admin).json()["totals"]["total"] == 2
    assert client.get("/api/admin/users", headers=admin).status_code == 200
    assert client.get("/api/admin/audit", headers=admin).status_code == 200
    assert repo.get_user(DEMO_ACCOUNTS["analyst"])["role"] == "analyst"


def test_demo_seed_is_small_idempotent_and_passwordless_only(demo, monkeypatch):
    repo, client = demo
    initialize_demo(repo, hash_password, main.model_id, main.artifact_hash)
    with repo._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM loan_applications").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM ml_predictions").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM manual_reviews").fetchone()[0] == 1
    assert client.post("/auth/register", json={"username": DEMO_ACCOUNTS["admin"], "password": "secret"}).status_code == 409
    assert client.post("/auth/demo/owner").status_code == 404
    monkeypatch.setattr(main, "DEMO_ENABLED", False)
    assert client.get("/auth/demo").json() == {"enabled": False}
    assert client.post("/auth/demo/admin").status_code == 404


def test_real_four_role_workflow_and_upload_limits(demo):
    repo, client = demo
    for username, role in (("private-analyst", "analyst"), ("private-reviewer", "reviewer"), ("private-admin", "admin")):
        repo.create_user(username, hash_password("private-fictional-test"), role)
    def headers(username, register=False):
        response = client.post("/auth/register" if register else "/auth/login", json={"username": username, "password": "private-fictional-test"})
        assert response.status_code == 200
        return {"Authorization": "Bearer " + response.json()["access_token"]}
    applicant, analyst, reviewer = headers("own-applicant", True), headers("private-analyst"), headers("private-reviewer")
    snapshot = json.loads((Path(__file__).parents[1] / "Data" / "recruiter_demo.json").read_text())["snapshot"]
    response = client.post("/api/applicant/applications", headers=applicant, json={"applicant_snapshot": snapshot})
    assert response.status_code == 201
    app_id = response.json()["application_id"]
    intake = client.get("/api/analyst/intake", headers=analyst).json()["applications"][0]
    assert client.post(f"/api/analyst/intake/{app_id}/claim", headers=analyst, json={"reviewer_username": "private-reviewer", "expected_updated_at": intake["updated_at"]}).status_code == 200
    assessment = client.post(f"/api/applications/{app_id}/assess", headers=analyst)
    assert assessment.status_code == 200
    assert len(assessment.json()["result"]["shap_values"]) == 20
    assert client.post(f"/api/applications/{app_id}/reviews", headers=reviewer, json={"prediction_id": assessment.json()["prediction_id"], "reviewer_decision": "approved", "review_notes": "Fictional workflow test", "override_reason": "Test approval"}).status_code == 200
    assert client.get(f"/api/applicant/applications/{app_id}", headers=applicant).json()["status"] == "approved"
    assert client.get("/api/admin/overview", headers=headers("private-admin")).json()["totals"]["approved"] == 2
    assert client.post("/predict_batch", headers=analyst, json=[snapshot] * 51).status_code == 422
    oversized = client.post("/api/csv-analysis", headers=analyst, files={"file": ("big.csv", b"x" * (1024 * 1024 + 1), "text/csv")})
    assert oversized.status_code == 413
    csv = pd.DataFrame([snapshot] * 51).to_csv(index=False).encode()
    assert client.post("/api/csv-analysis", headers=analyst, files={"file": ("rows.csv", csv, "text/csv")}).status_code == 400
