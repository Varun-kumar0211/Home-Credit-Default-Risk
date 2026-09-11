from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApplicationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

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