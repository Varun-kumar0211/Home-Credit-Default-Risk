import io
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, List

import pandas as pd
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
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
from auth import hash_password, issue_token, register_user, require_auth
from Cleaning import artifact_hash, model_id, model_source, process_application
from config import AUTH_PASSWORD, AUTH_USERNAME, DATABASE_URL, DATASET_DB_PATH, FRONTEND_DIR
from repository import DatasetRepository
from schemas import ApplicationSchema, ApplicationSubmissionRequest, LoginRequest, RegisterRequest, ReviewSubmission

logger = logging.getLogger(__name__)
started_at = datetime.now(timezone.utc).isoformat()
app = FastAPI(title="Credit Scoring API Engine")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
datasets = DatasetRepository(DATASET_DB_PATH, database_url=DATABASE_URL)
datasets.ensure_user(AUTH_USERNAME, hash_password(AUTH_PASSWORD))
app.add_middleware(GZipMiddleware, minimum_size=1000)


def _current_user(username: str = Depends(require_auth)):
    user = datasets.get_user(username)
    if user is None:
        raise HTTPException(status_code=401, detail="User account is not available.")
    return user


def _application_for_user(application_id: str, user: dict):
    application = datasets.get_application(application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    role = user.get("role", "analyst")
    if role == "admin" or application["owner"] == user["username"]:
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
    return register_user(credentials, datasets)


@app.post("/api/applications")
def create_application(payload: ApplicationSubmissionRequest, user: dict = Depends(_current_user)):
    _require_analyst(user)
    try:
        validated_snapshot = ApplicationSchema(**payload.applicant_snapshot)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    if payload.reviewer_username:
        reviewer = datasets.get_user(payload.reviewer_username)
        if not reviewer or reviewer.get("role") not in {"reviewer", "admin"}:
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
    )
    datasets.add_audit_log(application_id, owner, "application_created", None, payload.model_dump())
    return {
        "application_id": application_id,
        "owner": owner,
        "status": "submitted",
        "source_type": payload.source_type,
    }


@app.get("/api/applications")
def list_applications(user: dict = Depends(_current_user)):
    if user.get("role") == "reviewer":
        applications = [
            item for item in datasets.list_applications()
            if item.get("assigned_reviewer") == user["username"]
        ]
    elif user.get("role") == "admin":
        applications = datasets.list_applications()
    else:
        applications = datasets.list_applications(owner=user["username"])
    return {"applications": applications}


@app.get("/api/applications/{application_id}")
def get_application(application_id: str, user: dict = Depends(_current_user)):
    return _application_for_user(application_id, user)


@app.post("/api/applications/{application_id}/assess")
def assess_application(application_id: str, user: dict = Depends(_current_user)):
    _require_analyst(user)
    application = _application_for_user(application_id, user)
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
    )
    datasets.add_audit_log(application_id, user["username"], "prediction_generated", None, {"prediction_id": prediction_id})
    return {"application_id": application_id, "prediction_id": prediction_id, "result": result.model_dump()}


@app.get("/api/applications/{application_id}/predictions")
def list_predictions(application_id: str, user: dict = Depends(_current_user)):
    _application_for_user(application_id, user)
    return {"predictions": datasets.list_predictions(application_id)}


@app.post("/api/applications/{application_id}/reviews")
def submit_review(application_id: str, payload: ReviewSubmission, user: dict = Depends(_current_user)):
    _require_reviewer(user)
    application = _application_for_user(application_id, user)
    if application["status"] in {"approved", "rejected"}:
        raise HTTPException(status_code=409, detail="Reassessment is required before another final review.")
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
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    datasets.add_audit_log(application_id, user["username"], "manual_review", application["status"], status)
    return {"review_id": review_id, "application_id": application_id, "is_override": is_override}


@app.get("/api/applications/{application_id}/reviews")
def get_reviews(application_id: str, user: dict = Depends(_current_user)):
    _application_for_user(application_id, user)
    return {"reviews": datasets.list_reviews(application_id)}


@app.get("/api/applications/{application_id}/audit")
def get_audit(application_id: str, user: dict = Depends(_current_user)):
    _application_for_user(application_id, user)
    return {"audit": datasets.list_audit_logs(application_id)}


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
def manual_assessment(data: ApplicationSchema, _: str = Depends(require_auth)):
    try:
        return _prediction_result(data.model_dump())
    except Exception as exc:
        logger.exception("Manual assessment failed")
        raise HTTPException(status_code=500, detail="Prediction could not be completed") from exc


@app.get("/api/default-data")
def default_data(_: str = Depends(require_auth)):
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
def predict_default_applicant(applicant_id: str, owner: str = Depends(require_auth)):
    return _predict_dataset_applicant("default-demo", applicant_id, owner)


async def _upload_csv(file: UploadFile, owner: str, add_to_current: bool = False):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a CSV file.")
    try:
        dataframe = pd.read_csv(io.BytesIO(await file.read()))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read CSV: {exc}") from exc
    if dataframe.empty:
        raise HTTPException(status_code=400, detail="The uploaded CSV has no rows.")
    if len(dataframe) > 1000:
        raise HTTPException(status_code=400, detail="CSV files are limited to 1,000 applicants.")

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
    owner: str = Depends(require_auth),
):
    return await _upload_csv(file, owner, add_to_current)


@app.post("/api/upload-csv")
async def upload_csv(
    file: UploadFile = File(...),
    add_to_current: bool = Form(False),
    owner: str = Depends(require_auth),
):
    return await _upload_csv(file, owner, add_to_current)


@app.post("/api/datasets/{dataset_id}/applicants/{applicant_id}/predict", response_model=ApplicantAssessmentResponse)
def predict_uploaded_applicant(
    dataset_id: str, applicant_id: str, owner: str = Depends(require_auth)
):
    return _predict_dataset_applicant(dataset_id, applicant_id, owner)


@app.post("/predict", response_model=PredictionResponse)
def predict_single(data: ApplicationSchema, _: str = Depends(require_auth)):
    return manual_assessment(data, _)


@app.post("/predict_batch")
def predict_batch(
    data: Annotated[List[ApplicationSchema], Body(min_length=1, max_length=1000)],
    _: str = Depends(require_auth),
):
    return [_prediction_result(item.model_dump()).model_dump() for item in data]


@app.post("/predict_batch_analysis")
def predict_batch_analysis(
    data: Annotated[List[ApplicationSchema], Body(min_length=1, max_length=1000)],
    _: str = Depends(require_auth),
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
            "fields": exc.errors(),
        },
    )


@app.get("/health")
def health():
    return {"status": "ok", "model": model_source, "started_at": started_at}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000)
