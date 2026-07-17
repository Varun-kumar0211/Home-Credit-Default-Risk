import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from typing import List, Union

# Ensure the backend directory is on sys.path for module imports
backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from Cleaning import process_application

app = FastAPI(title="Credit Scoring API Engine")
class ApplicationSchema(BaseModel):
    GENDER: str
    QUALIFICATION: str
    FAMILY_STATUS: str
    OCCUPATION: str
    CONTRACT_TYPE: str
    TOTAL_INCOME: float
    CREDIT_AMOUNT: float
    ANNUAL_LOAN_PAYMENT: float
    GOODS_PRICE: float
    AGE: int
    YEARS_OF_EXPERIENCE: float
    CREDIT_SCORE: int
    CREDIT_HISTORY: int

@app.post("/predict")
def predict_single(data: ApplicationSchema):
    try:
        result=process_application(data.model_dump())
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/predict_batch")
def predict_batch(data:List[ApplicationSchema] = Body(...)):
    try:
        results = [process_application(item.model_dump()) for item in data]
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
if __name__ == "__main__":
    import uvicorn
    reload_flag = os.environ.get("DEV_RELOAD", "false").lower() in ("1", "true", "yes")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=reload_flag)


# Lightweight health endpoint used by the container startup script
@app.get("/health")
def health():
    return {"status": "ok"}
