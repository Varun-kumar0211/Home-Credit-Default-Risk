from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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