import io
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, List

import pandas as pd
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

backend_dir = Path(__file__).resolve().parent
project_root = backend_dir.parent
for import_path in (backend_dir, project_root):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from analytics import analyze_batch, analyze_scored_batch
from api_models import ApplicantAssessmentResponse, PredictionResponse
from auth import hash_password, issue_token, issue_account_token, register_user, require_auth, validate_account
from Cleaning import artifact_hash, model_id, model_source, process_application
from config import AUTH_PASSWORD, AUTH_USERNAME, DATABASE_URL, DATASET_DB_PATH, FRONTEND_DIR, DEMO_ENABLED, MAX_BATCH_ROWS, MAX_UPLOAD_BYTES
from Backend.demo import DEMO_ACCOUNTS, initialize_demo
from Backend.repository import AssignmentError, DatasetRepository
from schemas import ApplicantSubmission, IntakeClaim, ApplicationSchema, ApplicationSubmissionRequest, AssignmentUpdate, LoginRequest, RegisterRequest, ReviewSubmission, UserRoleUpdate, UserStatusUpdate

logger = logging.getLogger(__name__)
started_at = datetime.now(timezone.utc).isoformat()
app = FastAPI(title="Credit Scoring API Engine")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
datasets = DatasetRepository(DATASET_DB_PATH, database_url=DATABASE_URL)
datasets.ensure_user(AUTH_USERNAME, hash_password(AUTH_PASSWORD))
app.add_middleware(GZipMiddleware, minimum_size=1000)


def _current_user(request: Request, payload: dict = Depends(require_auth)):
    user = validate_account(payload, datasets)
    if user["username"] in DEMO_ACCOUNTS.values() and request.method not in {"GET", "HEAD"}:
        raise HTTPException(status_code=403, detail="Recruiter demo is read-only. Create your own Applicant account to submit an application.")
    if user["role"] == "applicant" and request.url.path != "/auth/me" and not request.url.path.startswith("/api/applicant/"):
        raise HTTPException(status_code=403, detail="Staff access is required.")
    return user


def _analyst_username(user: dict = Depends(_current_user)):
    _require_analyst(user)
    return user["username"]


def _application_for_user(application_id: str, user: dict):
    if user.get("role") not in {"analyst", "reviewer", "admin"}:
        raise HTTPException(status_code=403, detail="Staff access is required.")
    application = datasets.get_application(application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    role = user.get("role", "analyst")
    if role == "admin" or (role == "analyst" and ((application["source_type"] != "applicant" and application["owner"] == user["username"]) or application.get("assigned_analyst") == user["username"])):
        return application
    if role == "reviewer" and application.get("assigned_reviewer") == user["username"]:
        return application
    raise HTTPException(status_code=404, detail="Application not found.")


def _require_analyst(user: dict):
    if user.get("role") not in {"analyst", "admin"}:
        raise HTTPException(status_code=403, detail="Analyst access is required.")


def _require_reviewer(user: dict):
    if user.get("role") not in {"reviewer", "admin"}:
        raise HTTPException(status_code=403, detail="Reviewer access is required.")


def _require_admin(user: dict):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access is required.")


@app.exception_handler(AssignmentError)
async def repository_error_handler(request: Request, exc: AssignmentError):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.middleware("http")
async def disable_response_caching(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/")
def root():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.post("/auth/login")
def login(credentials: LoginRequest):
    return issue_token(credentials, datasets)


@app.post("/auth/register")
def register(credentials: RegisterRequest):
    if credentials.username in DEMO_ACCOUNTS.values():
        raise HTTPException(status_code=409, detail="This username is reserved for the recruiter demo.")
    return register_user(credentials, datasets)


@app.get("/auth/demo")
def demo_availability():
    return {"enabled": False}


@app.post("/auth/demo/{role}")
def demo_login(role: str):
    raise HTTPException(status_code=404, detail="Password-free demo access has been removed. Sign in with your account.")


@app.get("/auth/me")
def me(user: dict = Depends(_current_user)):
    identity = {"username": user["username"], "role": user.get("role", "analyst")}
    if user["username"] in DEMO_ACCOUNTS.values():
        identity["read_only"] = True
    return identity


@app.get("/api/reviewers")
def list_reviewers(user: dict = Depends(_current_user)):
    """Return usernames eligible for loan-application assignment."""
    _require_analyst(user)
    with datasets._connection() as connection:
        rows = connection.execute(
            datasets._sql("SELECT username FROM users WHERE role='reviewer' AND is_active=1 ORDER BY username ASC")
        ).fetchall()
    return {"reviewers": [row[0] for row in rows]}


@app.post("/api/applications")
def create_application(payload: ApplicationSubmissionRequest, user: dict = Depends(_current_user)):
    _require_analyst(user)
    if payload.source_type == "applicant":
        raise HTTPException(status_code=422, detail="Applicant source is reserved for portal submissions.")
    try:
        validated_snapshot = ApplicationSchema(**payload.applicant_snapshot)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    if payload.reviewer_username:
        reviewer = datasets.get_user(payload.reviewer_username)
        if not reviewer or reviewer.get("role") != "reviewer" or not reviewer["is_active"]:
            raise HTTPException(status_code=400, detail="Assigned reviewer is not authorized.")
    owner = user["username"]
    application_id = datasets.create_application(
        owner=owner,
        source_type=payload.source_type,
        applicant_snapshot=validated_snapshot.model_dump(),
        dataset_id=payload.dataset_id,
        source_applicant_id=payload.source_applicant_id,
        assigned_reviewer=payload.reviewer_username,
        status="submitted",
        actor_version=user["version"],
    )
    datasets.add_audit_log(application_id, owner, "application_created", None, payload.model_dump(exclude={"applicant_snapshot"}))
    return {
        "application_id": application_id,
        "owner": owner,
        "status": "submitted",
        "source_type": payload.source_type,
    }


@app.get("/api/applications")
def list_applications(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0), user: dict = Depends(_current_user)):
    if user["role"] not in {"analyst", "reviewer", "admin"}:
        raise HTTPException(status_code=403, detail="Staff access is required.")
    return datasets.application_page(role=user["role"], username=user["username"], limit=limit, offset=offset)


def _require_applicant(user):
    if user["role"] != "applicant":
        raise HTTPException(status_code=403, detail="Applicant access is required.")


@app.post("/api/applicant/applications", status_code=201)
def applicant_submit(payload: ApplicantSubmission, user: dict = Depends(_current_user)):
    _require_applicant(user)
    snapshot = payload.applicant_snapshot.model_dump(exclude={"APPROVE_THRESHOLD", "DECLINE_THRESHOLD"})
    application_id = datasets.create_application(user["username"], "applicant", snapshot, actor_version=user["version"])
    return datasets.public_application(datasets.get_application(application_id))


@app.get("/api/applicant/applications")
def applicant_list(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0), user: dict = Depends(_current_user)):
    _require_applicant(user)
    return datasets.application_page(role="applicant", username=user["username"], limit=limit, offset=offset)


@app.get("/api/applicant/applications/{application_id}")
def applicant_detail(application_id: str, user: dict = Depends(_current_user)):
    _require_applicant(user)
    application = datasets.get_application(application_id, owner=user["username"])
    if not application or application["source_type"] != "applicant":
        raise HTTPException(status_code=404, detail="Application not found.")
    return datasets.public_application(application, detail=True)


@app.get("/api/analyst/portfolio")
def analyst_portfolio(user: dict = Depends(_current_user)):
    if user["role"] != "analyst":
        raise HTTPException(status_code=403, detail="Analyst access is required.")
    return datasets.get_analyst_portfolio_summary()


@app.get("/api/analyst/intake")
def analyst_intake(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0), user: dict = Depends(_current_user)):
    if user["role"] != "analyst":
        raise HTTPException(status_code=403, detail="Analyst access is required.")
    return datasets.application_page(role="analyst", username=user["username"], limit=limit, offset=offset, intake=True)


@app.post("/api/analyst/intake/{application_id}/claim")
def analyst_claim(application_id: str, payload: IntakeClaim, user: dict = Depends(_current_user)):
    if user["role"] != "analyst":
        raise HTTPException(status_code=403, detail="Analyst access is required.")
    return datasets.claim_application(application_id, user["username"], user["version"], payload.reviewer_username, payload.expected_updated_at)


@app.get("/api/applications/{application_id}")
def get_application(application_id: str, user: dict = Depends(_current_user)):
    return _application_for_user(application_id, user)


@app.post("/api/applications/{application_id}/assess")
def assess_application(application_id: str, user: dict = Depends(_current_user)):
    _require_analyst(user)
    application = _application_for_user(application_id, user)
    if application["status"] in {"approved", "rejected"}:
        raise HTTPException(status_code=409, detail="Finalized applications cannot be reassessed.")
    if application["source_type"] == "applicant" and not application.get("assigned_analyst"):
        raise HTTPException(status_code=409, detail="An Analyst must claim this application before assessment.")
    applicant = application["applicant_snapshot"]
    raw_result = process_application(applicant)
    result = PredictionResponse(
        decision=raw_result["decision"],
        risk_tier=raw_result["risk_tier"],
        default_probability=raw_result["default_probability"],
        risk_score=raw_result["risk_score"],
        trust_score=raw_result["trust_score"],
        confidence=raw_result["confidence"],
        approve_threshold=raw_result["approve_threshold"],
        decline_threshold=raw_result["decline_threshold"],
        recommended_rate=raw_result["recommended_rate"],
        loan_amount_decision=raw_result["loan_amount_decision"],
        explanations=raw_result["explanations"],
        shap_values=raw_result["shap_values"],
    )
    datasets.ensure_model_version(
        model_id,
        {
            "model_source": model_source,
            "model_version": model_id,
            "artifact_hash": artifact_hash,
            "feature_schema": [item.feature for item in result.shap_values],
            "preprocessing_version": "Cleaning.process_application",
        },
    )
    prediction_payload = {
        "default_probability": result.default_probability,
        "risk_score": result.risk_score,
        "trust_score": result.trust_score,
        "confidence": result.confidence,
        "risk_tier": result.risk_tier,
        "decision": result.decision,
        "approve_threshold": result.approve_threshold,
        "decline_threshold": result.decline_threshold,
        "recommended_rate": result.recommended_rate,
        "loan_amount_decision": result.loan_amount_decision,
        "input_snapshot": applicant,
        "processed_feature_snapshot": raw_result["processed_feature_snapshot"],
    }
    prediction_id = datasets.persist_prediction_assessment(
        application_id,
        model_id,
        prediction_payload,
        {
            "base_value": raw_result["shap_base_value"],
            "output_space": raw_result["shap_output_space"],
            "feature_contributions": [item.model_dump() for item in result.shap_values],
        },
        actor=user["username"], actor_version=user["version"], expected_updated_at=application["updated_at"],
    )
    return {"application_id": application_id, "prediction_id": prediction_id, "result": result.model_dump()}


@app.get("/api/applications/{application_id}/predictions")
def list_predictions(application_id: str, user: dict = Depends(_current_user)):
    _application_for_user(application_id, user)
    return {"predictions": datasets.list_predictions(application_id)}


@app.get("/api/applications/{application_id}/predictions/{prediction_id}/explanation")
@app.get("/api/applications/{application_id}/predictions/{prediction_id}/shap")
def get_prediction_explanation(
    application_id: str, prediction_id: str, user: dict = Depends(_current_user)
):
    _application_for_user(application_id, user)
    predictions = datasets.list_predictions(application_id)
    if not any(p["prediction_id"] == prediction_id for p in predictions):
        raise HTTPException(status_code=404, detail="Prediction not found for this application.")
    explanation = datasets.get_shap_explanation(prediction_id)
    if explanation is None:
        raise HTTPException(status_code=404, detail="Explanation not found for this prediction.")
    return {"explanation": explanation}


@app.post("/api/applications/{application_id}/reviews")
def submit_review(application_id: str, payload: ReviewSubmission, user: dict = Depends(_current_user)):
    _require_reviewer(user)
    application = _application_for_user(application_id, user)
    if application["status"] in {"approved", "rejected"}:
        raise HTTPException(status_code=409, detail="Finalized applications cannot be reviewed again.")
    predictions = datasets.list_predictions(application_id)
    prediction = next(
        (item for item in predictions if item["prediction_id"] == payload.prediction_id),
        predictions[0] if payload.prediction_id is None and predictions else None,
    )
    if prediction is None:
        raise HTTPException(status_code=400, detail="A prediction for this application is required.")
    expected_decision = {
        "Auto Approve": "approved",
        "Auto Decline": "rejected",
        "Manual Review Required": "request_information",
    }.get(prediction["model_decision"])
    is_override = expected_decision != payload.reviewer_decision
    if is_override and not payload.override_reason:
        raise HTTPException(status_code=422, detail="override_reason is required when overriding the model.")
    status = {
        "approved": "approved",
        "rejected": "rejected",
        "request_information": "needs_information",
    }[payload.reviewer_decision]
    try:
        review_id = datasets.finalize_review(
            application_id,
            prediction["prediction_id"],
            user["username"],
            payload.reviewer_decision,
            payload.review_notes,
            payload.override_reason,
            is_override,
            expected_updated_at=application["updated_at"],
            actor_version=user["version"],
        )
    except AssignmentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"review_id": review_id, "application_id": application_id, "is_override": is_override}


@app.get("/api/applications/{application_id}/reviews")
def get_reviews(application_id: str, user: dict = Depends(_current_user)):
    _application_for_user(application_id, user)
    return {"reviews": datasets.list_reviews(application_id)}


@app.get("/api/applications/{application_id}/audit")
def get_audit(application_id: str, user: dict = Depends(_current_user)):
    _application_for_user(application_id, user)
    return {"audit": [datasets.sanitize_audit(event) for event in datasets.list_audit_logs(application_id)]}


@app.get("/api/reviewers/assigned")
def get_assigned_applications(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0), user: dict = Depends(_current_user)):
    if user["role"] not in {"analyst", "reviewer", "admin"}:
        raise HTTPException(status_code=403, detail="Staff access is required.")
    return datasets.application_page(role=user["role"], username=user["username"], limit=limit, offset=offset)


@app.get("/api/reviewers/pending")
def get_pending_reviews(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0), user: dict = Depends(_current_user)):
    if user["role"] == "analyst":
        return {"applications": [], "total": 0, "limit": limit, "offset": offset}
    _require_reviewer(user)
    return datasets.application_page(role=user["role"], username=user["username"], limit=limit, offset=offset, pending=True)


@app.get("/api/reviewers/history")
def get_review_history(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0), user: dict = Depends(_current_user)):
    if user["role"] == "analyst":
        return {"history": [], "total": 0, "limit": limit, "offset": offset}
    _require_reviewer(user)
    return datasets.review_history_page(user["username"], limit, offset)


@app.get("/api/admin/overview")
def admin_portfolio_overview(user: dict = Depends(_current_user)):
    """Return portfolio KPIs and reviewer workload for Admin role only."""
    _require_admin(user)
    return datasets.get_admin_portfolio_overview()


@app.get("/api/admin/assignments")
def admin_assignments(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(_current_user),
):
    _require_admin(user)
    page = datasets.list_admin_assignments(limit, offset)
    eligible = set(list_reviewers(user)["reviewers"])
    page["reviewer_workload"] = [row for row in datasets.get_admin_portfolio_overview()["reviewer_workload"]
                                 if row["username"] in eligible]
    return page


@app.patch("/api/admin/applications/{application_id}/assignment")
def admin_reassign_application(application_id: str, payload: AssignmentUpdate, user: dict = Depends(_current_user)):
    _require_admin(user)
    try:
        return datasets.reassign_application(
            application_id, user["username"], payload.reviewer_username,
            payload.reason, payload.expected_updated_at, actor_version=user["version"],
        )
    except AssignmentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.get("/api/admin/users")
def admin_users(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
                user: dict = Depends(_current_user)):
    _require_admin(user)
    return datasets.list_users(limit, offset)


@app.patch("/api/admin/users/{username}/role")
def admin_user_role(username: str, payload: UserRoleUpdate, user: dict = Depends(_current_user)):
    _require_admin(user)
    return datasets.change_user(user["username"], user["version"], username,
                                payload.expected_version, payload.reason, role=payload.role)


@app.patch("/api/admin/users/{username}/status")
def admin_user_status(username: str, payload: UserStatusUpdate, user: dict = Depends(_current_user)):
    _require_admin(user)
    return datasets.change_user(user["username"], user["version"], username,
                                payload.expected_version, payload.reason, is_active=payload.is_active)


@app.get("/api/admin/audit")
def admin_audit(limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
                event_type: str | None = None, actor: str | None = None,
                application_id: str | None = None, date_from: datetime | None = None,
                date_to: datetime | None = None, user: dict = Depends(_current_user)):
    _require_admin(user)
    def utc(value):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    start, end = utc(date_from) if date_from else None, utc(date_to) if date_to else None
    if start and end and start > end:
        raise HTTPException(status_code=422, detail="date_from must precede date_to.")
    return datasets.list_admin_audit(limit, offset, event_type, actor, application_id,
                                    start.isoformat() if start else None, end.isoformat() if end else None)


def _prediction_result(raw_data: dict) -> PredictionResponse:
    result = process_application(raw_data)
    return PredictionResponse(
        decision=result["decision"],
        risk_tier=result["risk_tier"],
        default_probability=result["default_probability"],
        risk_score=result["risk_score"],
        trust_score=result["trust_score"],
        confidence=result["confidence"],
        approve_threshold=result["approve_threshold"],
        decline_threshold=result["decline_threshold"],
        recommended_rate=result["recommended_rate"],
        loan_amount_decision=result["loan_amount_decision"],
        explanations=result["explanations"],
        shap_values=result["shap_values"],
    )


def _demo_dataset():
    seed_path = project_root / "Data" / "model_test_cases.csv"
    if not seed_path.exists():
        raise HTTPException(
            status_code=503,
            detail=f"Seed dataset is missing: {seed_path}. Restore Data/model_test_cases.csv and restart the service.",
        )
    dataframe = pd.read_csv(seed_path)
    applicants = []
    for index, row in dataframe.iterrows():
        applicants.append({
            "id": f"DEMO-{index + 1:04d}",
            "row_number": index + 1,
            "data": {
                str(key): (
                    None if pd.isna(value)
                    else value.item() if hasattr(value, "item") else value
                )
                for key, value in row.to_dict().items()
            },
            "validation": [],
        })
    added = datasets.get_current_applicants()
    if added:
        added_frame = pd.DataFrame([item["data"] for item in added])
        dataframe = pd.concat([dataframe, added_frame], ignore_index=True, sort=False)
        start = len(applicants)
        for offset, item in enumerate(added, start=1):
            item["row_number"] = start + offset
        applicants.extend(added)
    return dataframe, applicants


def _dataset_payload(dataset_id, filename, dataframe, applicants):
    valid_applicants = [item for item in applicants if not item.get("validation")]
    scored_applicants = []
    results = []
    for item in valid_applicants:
        try:
            validated = ApplicationSchema(**item["data"])
            result = _prediction_result(validated.model_dump()).model_dump()
            item["assessment"] = result
            scored_applicants.append(item)
            results.append(result)
        except Exception:
            item["validation"] = ["The model could not process this applicant."]
    baseline_path = project_root / "Data" / "application_train.csv"
    baseline = pd.read_csv(baseline_path, usecols=lambda column: column in dataframe.columns) if baseline_path.exists() else None
    analysis = analyze_scored_batch(
        dataframe,
        [item["data"] for item in scored_applicants],
        results,
        baseline,
    )
    analysis["chart"] = {
        "title": "Predicted default-risk distribution",
        **analysis["risk_probability_histogram"],
    }
    return {
        "dataset_id": dataset_id,
        "filename": filename,
        "analysis": analysis,
        "applicants": applicants,
    }


@app.post("/api/manual-assessment", response_model=PredictionResponse)
def manual_assessment(data: ApplicationSchema, _: str = Depends(_analyst_username)):
    try:
        return _prediction_result(data.model_dump())
    except Exception as exc:
        logger.exception("Manual assessment failed")
        raise HTTPException(status_code=500, detail="Prediction could not be completed") from exc


@app.get("/api/default-data")
def default_data(_: str = Depends(_analyst_username)):
    dataframe, applicants = _demo_dataset()
    return _dataset_payload("default-demo", "Current dataset (model_test_cases.csv)", dataframe, applicants)


def _predict_dataset_applicant(dataset_id, applicant_id, owner):
    if dataset_id == "default-demo":
        _, applicants = _demo_dataset()
        applicant = next((item for item in applicants if item["id"] == applicant_id), None)
    else:
        applicant = datasets.get_applicant(dataset_id, applicant_id, owner)
    if applicant is None:
        raise HTTPException(status_code=404, detail="Dataset or applicant not found.")
    if applicant.get("validation"):
        raise HTTPException(status_code=422, detail={"row": applicant["row_number"], "errors": applicant["validation"]})
    try:
        validated = ApplicationSchema(**applicant["data"])
        result = _prediction_result(validated.model_dump())
        return ApplicantAssessmentResponse(
            applicant_id=applicant_id,
            row_number=applicant["row_number"],
            result=result,
        )
    except Exception as exc:
        logger.exception("Applicant prediction failed")
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/default-data/applicants/{applicant_id}/predict", response_model=ApplicantAssessmentResponse)
def predict_default_applicant(applicant_id: str, owner: str = Depends(_analyst_username)):
    return _predict_dataset_applicant("default-demo", applicant_id, owner)


async def _upload_csv(file: UploadFile, owner: str, add_to_current: bool = False):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a CSV file.")
    contents = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="CSV uploads are limited to 1 MiB.")
    try:
        dataframe = pd.read_csv(io.BytesIO(contents))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read CSV: {exc}") from exc
    if dataframe.empty:
        raise HTTPException(status_code=400, detail="The uploaded CSV has no rows.")
    if len(dataframe) > MAX_BATCH_ROWS:
        raise HTTPException(status_code=400, detail=f"CSV files are limited to {MAX_BATCH_ROWS} applicants.")

    try:
        dataset_id = uuid.uuid4().hex
        applicants = []
        for index, row in dataframe.iterrows():
            data = {
                str(key): None if pd.isna(value) else value.item() if hasattr(value, "item") else value
                for key, value in row.to_dict().items()
            }
            validation = []
            try:
                ApplicationSchema(**data)
            except ValidationError as exc:
                validation = [error["msg"] for error in exc.errors()]
            except Exception as exc:
                validation = [str(exc)]
            applicants.append({
                "id": f"APP-{dataset_id[:8].upper()}-{index + 1:04d}",
                "row_number": index + 1,
                "data": data,
                "validation": validation,
            })

        valid_applicants = [item for item in applicants if not item["validation"]]
        scored_applicants = []
        results = []
        for item in valid_applicants:
            try:
                validated = ApplicationSchema(**item["data"])
                result = _prediction_result(validated.model_dump()).model_dump()
                item["assessment"] = result
                scored_applicants.append(item)
                results.append(result)
            except Exception:
                item["validation"] = ["The model could not process this applicant."]
        baseline_path = project_root / "Data" / "application_train.csv"
        baseline = pd.read_csv(baseline_path, usecols=lambda column: column in dataframe.columns) if baseline_path.exists() else None
        analysis = analyze_scored_batch(
            dataframe,
            [item["data"] for item in scored_applicants],
            results,
            baseline,
        )
        analysis["valid_rows"] = sum(not item["validation"] for item in applicants)
        analysis["invalid_rows"] = len(applicants) - analysis["valid_rows"]
        analysis["chart"] = {
            "title": "Predicted default-risk distribution",
            **analysis["risk_probability_histogram"],
        }
        datasets.save_dataset(dataset_id, owner, file.filename, analysis, applicants)
        if add_to_current:
            datasets.append_current(scored_applicants)
        return {
            "dataset_id": dataset_id,
            "filename": file.filename,
            "analysis": analysis,
            "applicants": applicants,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("CSV upload failed for %s", file.filename)
        raise HTTPException(
            status_code=503,
            detail=f"CSV upload could not be completed: {exc}",
        ) from exc


@app.post("/api/csv-analysis")
async def csv_analysis(
    file: UploadFile = File(...),
    add_to_current: bool = Form(False),
    owner: str = Depends(_analyst_username),
):
    return await _upload_csv(file, owner, add_to_current)


@app.post("/api/upload-csv")
async def upload_csv(
    file: UploadFile = File(...),
    add_to_current: bool = Form(False),
    owner: str = Depends(_analyst_username),
):
    return await _upload_csv(file, owner, add_to_current)


@app.post("/api/datasets/{dataset_id}/applicants/{applicant_id}/predict", response_model=ApplicantAssessmentResponse)
def predict_uploaded_applicant(
    dataset_id: str, applicant_id: str, owner: str = Depends(_analyst_username)
):
    return _predict_dataset_applicant(dataset_id, applicant_id, owner)


@app.post("/predict", response_model=PredictionResponse)
def predict_single(data: ApplicationSchema, _: str = Depends(_analyst_username)):
    return manual_assessment(data, _)


@app.post("/predict_batch")
def predict_batch(
    data: Annotated[List[ApplicationSchema], Body(min_length=1, max_length=MAX_BATCH_ROWS)],
    _: str = Depends(_analyst_username),
):
    return [_prediction_result(item.model_dump()).model_dump() for item in data]


@app.post("/predict_batch_analysis")
def predict_batch_analysis(
    data: Annotated[List[ApplicationSchema], Body(min_length=1, max_length=MAX_BATCH_ROWS)],
    _: str = Depends(_analyst_username),
):
    applications = [item.model_dump() for item in data]
    results = [_prediction_result(item).model_dump() for item in applications]
    return {"results": results, "analytics": analyze_batch(applications, results)}


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "message": "Input values do not match the supported applicant schema.",
            "fields": [{key: error[key] for key in ("loc", "msg", "type")}
                       for error in exc.errors()],
        },
    )


@app.get("/health")
def health():
    return {"status": "ok", "model": model_source, "started_at": started_at}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000)
