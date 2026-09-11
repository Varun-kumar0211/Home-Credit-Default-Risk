import os
import sys
from pathlib import Path

import logging
from datetime import datetime, timezone
from typing import Annotated, List

from fastapi import FastAPI, Body, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# Ensure the backend directory is on sys.path for module imports
backend_dir = Path(__file__).resolve().parent
project_root = backend_dir.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from schemas import ApplicationSchema
from Cleaning import model_source, process_application

logger = logging.getLogger(__name__)
started_at = datetime.now(timezone.utc).isoformat()

app = FastAPI(title="Credit Scoring API Engine")


@app.middleware("http")
async def disable_response_caching(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/")
def root():
    return {
        "service": "Credit Scoring API Engine",
        "status": "ok",
        "model": model_source,
        "health": "/health",
        "docs": "/docs",
        "single_prediction": "/predict",
        "batch_prediction": "/predict_batch",
    }


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


@app.post("/predict")
def predict_single(data: ApplicationSchema):
    try:
        result=process_application(data.model_dump())
        return result
    except Exception as e:
        logger.exception("Single prediction failed")
        raise HTTPException(status_code=500, detail="Prediction could not be completed") from e
    
@app.post("/predict_batch")
def predict_batch(
    data: Annotated[List[ApplicationSchema], Body(min_length=1, max_length=1000)],
):
    try:
        results = [process_application(item.model_dump()) for item in data]
        return results
    except Exception as e:
        logger.exception("Batch prediction failed")
        raise HTTPException(status_code=500, detail="Prediction could not be completed") from e
    
if __name__ == "__main__":
    import uvicorn
    reload_flag = os.environ.get("DEV_RELOAD", "false").lower() in ("1", "true", "yes")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=reload_flag)


# Lightweight health endpoint used by the container startup script
@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": model_source,
        "started_at": started_at,
    }
