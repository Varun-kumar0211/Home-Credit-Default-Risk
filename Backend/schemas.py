from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApplicationSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    GENDER: Literal["M", "F", "X"]
    QUALIFICATION: Literal[
        "Secondary / secondary special",
        "Higher education",
        "Incomplete higher",
        "Lower secondary",
        "Academic degree",
    ]
    FAMILY_STATUS: Literal[
        "Married",
        "Single / not married",
        "Civil marriage",
        "Separated",
        "Widow",
    ]
    OCCUPATION: Literal[
        "Laborers", "Core staff", "Sales staff", "Managers", "Drivers",
        "High skill tech staff", "Accountants", "Medicine staff",
        "Security staff", "Cooking staff",
    ]
    CONTRACT_TYPE: Literal["Cash loans", "Revolving loans"]
    TOTAL_INCOME: float = Field(gt=0)
    CREDIT_AMOUNT: float = Field(gt=0)
    ANNUAL_LOAN_PAYMENT: float = Field(gt=0)
    GOODS_PRICE: float = Field(gt=0)
    AGE: int = Field(ge=18, le=100)
    YEARS_OF_EXPERIENCE: float = Field(ge=0, le=82)
    CREDIT_SCORE: int = Field(ge=300, le=850)
    CREDIT_HISTORY: Literal[0, 1]
    APPROVE_THRESHOLD: float | None = Field(default=None, ge=0, lt=1)
    DECLINE_THRESHOLD: float | None = Field(default=None, gt=0, le=1)

    @model_validator(mode="after")
    def validate_realistic_relationships(self):
        if self.CREDIT_AMOUNT > self.TOTAL_INCOME * 100:
            raise ValueError("CREDIT_AMOUNT is outside the supported income range")
        if self.GOODS_PRICE > self.CREDIT_AMOUNT * 2:
            raise ValueError("GOODS_PRICE is outside the supported credit range")
        if self.YEARS_OF_EXPERIENCE > self.AGE - 14:
            raise ValueError("YEARS_OF_EXPERIENCE is not realistic for the applicant age")
        if (
            self.APPROVE_THRESHOLD is not None
            and self.DECLINE_THRESHOLD is not None
            and self.APPROVE_THRESHOLD >= self.DECLINE_THRESHOLD
        ):
            raise ValueError("APPROVE_THRESHOLD must be lower than DECLINE_THRESHOLD")
        return self


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class ApplicantInput(ApplicationSchema):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid", allow_inf_nan=False)
    APPROVE_THRESHOLD: ClassVar[None] = None
    DECLINE_THRESHOLD: ClassVar[None] = None

    @model_validator(mode="before")
    @classmethod
    def reject_internal_thresholds(cls, values):
        if isinstance(values, dict) and any(name in values for name in ("APPROVE_THRESHOLD", "DECLINE_THRESHOLD")):
            raise ValueError("Applicants cannot supply assessment thresholds")
        return values


class ApplicantSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    applicant_snapshot: ApplicantInput


class IntakeClaim(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    expected_updated_at: str = Field(min_length=1, max_length=100)
    reviewer_username: str = Field(min_length=1, max_length=100)


class RegisterRequest(LoginRequest):
    model_config = ConfigDict(extra="forbid")


class ApplicationSubmissionRequest(BaseModel):
    source_type: str = "manual"
    dataset_id: str | None = None
    source_applicant_id: str | None = None
    reviewer_username: str | None = None
    applicant_snapshot: dict


class ReviewSubmission(BaseModel):
    prediction_id: str | None = None
    reviewer_decision: Literal["approved", "rejected", "request_information"]
    review_notes: str = ""
    override_reason: str | None = None


class AssignmentUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    reviewer_username: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=2000)
    expected_updated_at: str = Field(min_length=1, max_length=100)


class UserChange(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    reason: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=0, strict=True)


class UserRoleUpdate(UserChange):
    role: Literal["analyst", "reviewer", "admin"]


class UserStatusUpdate(UserChange):
    is_active: bool = Field(strict=True)
