"""Two fictional, read-only recruiter examples; no inference during startup."""
import json
import secrets
from pathlib import Path

DEMO_ACCOUNTS = {role: f"recruiter-demo-{role}" for role in ("applicant", "analyst", "reviewer", "admin")}


def initialize_demo(repo, hash_password, model_id, artifact_hash):
    fixture = json.loads((Path(__file__).resolve().parent.parent / "Data" / "recruiter_demo.json").read_text())
    if fixture["artifact_hash"] != artifact_hash:
        raise RuntimeError("Recruiter fixture must match the deployed model artifact.")
    for role, username in DEMO_ACCOUNTS.items():
        repo.ensure_user(username, hash_password(secrets.token_urlsafe(32)), role=role)
        account = repo.get_user(username)
        if account["role"] != role:
            raise RuntimeError("Reserved demo account has an unexpected role.")
    repo.ensure_model_version(model_id, {"artifact_hash": artifact_hash, "model_source": "calibrated", "model_version": model_id, "feature_schema": [v["feature"] for v in fixture["explanation"]["feature_contributions"]], "preprocessing_version": "Cleaning.process_application"})
    for label in ("pending", "approved"):
        marker = "recruiter-example-" + label
        with repo._connection() as connection:
            row = connection.execute(repo._sql("SELECT application_id FROM loan_applications WHERE owner=? AND source_applicant_id=?"), (DEMO_ACCOUNTS["applicant"], marker)).fetchone()
        app_id = row[0] if row else repo.create_application(DEMO_ACCOUNTS["applicant"], "applicant", fixture["snapshot"], source_applicant_id=marker)
        application = repo.get_application(app_id)
        if not application.get("assigned_analyst"):
            analyst = repo.get_user(DEMO_ACCOUNTS["analyst"])
            repo.claim_application(app_id, analyst["username"], analyst["version"], DEMO_ACCOUNTS["reviewer"], application["updated_at"])
        predictions = repo.list_predictions(app_id)
        analyst = repo.get_user(DEMO_ACCOUNTS["analyst"])
        prediction_id = predictions[0]["prediction_id"] if predictions else repo.persist_prediction_assessment(app_id, model_id, fixture["prediction"], fixture["explanation"], actor=analyst["username"], actor_version=analyst["version"], expected_updated_at=repo.get_application(app_id)["updated_at"])
        if label == "approved" and repo.get_application(app_id)["status"] != "approved":
            repo.finalize_review(app_id, prediction_id, DEMO_ACCOUNTS["reviewer"], "approved", "Fictional recruiter example; no real lending decision.", None, False)
