import unittest

from pydantic import ValidationError

from Backend.Cleaning import _decision_for_probability, _normalize_threshold
from Backend.app import _extract_percent
from Backend.schemas import ApplicationSchema


VALID_APPLICATION = {
    "GENDER": "M",
    "QUALIFICATION": "Higher education",
    "FAMILY_STATUS": "Single / not married",
    "OCCUPATION": "Core staff",
    "CONTRACT_TYPE": "Cash loans",
    "TOTAL_INCOME": 50000,
    "CREDIT_AMOUNT": 150000,
    "ANNUAL_LOAN_PAYMENT": 12000,
    "GOODS_PRICE": 150000,
    "AGE": 30,
    "YEARS_OF_EXPERIENCE": 5,
    "CREDIT_SCORE": 710,
    "CREDIT_HISTORY": 0,
}


class ApplicationSchemaTests(unittest.TestCase):
    def test_accepts_form_payload(self):
        application = ApplicationSchema(**VALID_APPLICATION)

        self.assertEqual(application.GENDER, "M")
        self.assertEqual(application.CREDIT_HISTORY, 0)

    def test_rejects_invalid_financial_values(self):
        payload = {**VALID_APPLICATION, "TOTAL_INCOME": 0}

        with self.assertRaises(ValidationError):
            ApplicationSchema(**payload)

    def test_rejects_invalid_categories(self):
        payload = {**VALID_APPLICATION, "CONTRACT_TYPE": "Unknown loan"}

        with self.assertRaises(ValidationError):
            ApplicationSchema(**payload)

    def test_rejects_unknown_fields(self):
        payload = {**VALID_APPLICATION, "unexpected": "value"}

        with self.assertRaises(ValidationError):
            ApplicationSchema(**payload)

    def test_rejects_unrealistic_loan_to_income_ratio(self):
        payload = {**VALID_APPLICATION, "CREDIT_AMOUNT": 5_000_001}

        with self.assertRaises(ValidationError):
            ApplicationSchema(**payload)

    def test_rejects_experience_greater_than_working_age(self):
        payload = {**VALID_APPLICATION, "AGE": 18, "YEARS_OF_EXPERIENCE": 10}

        with self.assertRaises(ValidationError):
            ApplicationSchema(**payload)

    def test_accepts_custom_decision_thresholds(self):
        application = ApplicationSchema(
            **{
                **VALID_APPLICATION,
                "APPROVE_THRESHOLD": 0.10,
                "DECLINE_THRESHOLD": 0.25,
            }
        )

        self.assertEqual(application.APPROVE_THRESHOLD, 0.10)
        self.assertEqual(application.DECLINE_THRESHOLD, 0.25)

    def test_rejects_reversed_decision_thresholds(self):
        payload = {
            **VALID_APPLICATION,
            "APPROVE_THRESHOLD": 0.30,
            "DECLINE_THRESHOLD": 0.20,
        }

        with self.assertRaises(ValidationError):
            ApplicationSchema(**payload)

    def test_high_probability_always_declines(self):
        decision = _decision_for_probability(0.93, 0.08, 0.20)

        self.assertEqual(decision, ("Auto Decline", "Tier C", "N/A"))

    def test_percentage_thresholds_normalize_before_decision(self):
        approve = _normalize_threshold(8, 0.08)
        decline = _normalize_threshold(20, 0.20)

        self.assertEqual(_decision_for_probability(0.93, approve, decline)[0], "Auto Decline")

    def test_dashboard_preserves_explicit_percent_strings(self):
        self.assertEqual(_extract_percent("0.93%"), 0.93)
        self.assertEqual(_extract_percent("34.00%"), 34.0)
        self.assertEqual(_extract_percent("0.34"), 34.0)
        self.assertEqual(_extract_percent("0.93/100"), 0.93)


if __name__ == "__main__":
    unittest.main()
