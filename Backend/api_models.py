from pydantic import BaseModel, Field


class ExplanationItem(BaseModel):
    feature: str
    label: str
    value: float | str | None = None
    impact: float
    direction: str


class PredictionResponse(BaseModel):
    decision: str
    risk_tier: str
    default_probability: float = Field(ge=0, le=1)
    risk_score: float = Field(ge=0, le=100)
    trust_score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    approve_threshold: float = Field(ge=0, le=1)
    decline_threshold: float = Field(ge=0, le=1)
    recommended_rate: str
    loan_amount_decision: str
    explanations: dict[str, list[ExplanationItem]]
    shap_values: list[ExplanationItem] = Field(default_factory=list)


class ApplicantAssessmentResponse(BaseModel):
    applicant_id: str
    row_number: int
    result: PredictionResponse
