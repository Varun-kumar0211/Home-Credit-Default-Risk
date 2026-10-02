"""Batch scoring analytics built from model results and applicant attributes."""

from collections import Counter, defaultdict
import math
import re
from statistics import mean, median

import pandas as pd
import numpy as np


def _probability_as_percent(value) -> float:
    if isinstance(value, (int, float)):
        return float(value) * 100
    match = re.search(r"[-+]?\d*\.?\d+", str(value))
    return float(match.group()) if match else 0.0


def _segment_summary(applications, results, field: str) -> list[dict]:
    grouped = defaultdict(list)
    for application, result in zip(applications, results):
        grouped[str(application.get(field, "Unknown"))].append(result)

    summaries = []
    for segment, segment_results in sorted(grouped.items()):
        probabilities = [
            _probability_as_percent(
                result.get("default_probability", result.get("Probability of default", 0))
            )
            for result in segment_results
        ]
        summaries.append(
            {
                "segment": segment,
                "applicants": len(segment_results),
                "average_default_probability": round(mean(probabilities), 2),
                "declines": sum(
                    result.get("decision", result.get("Final Decision")) == "Auto Decline"
                    for result in segment_results
                ),
                "approvals": sum(
                    result.get("decision", result.get("Final Decision")) == "Auto Approve"
                    for result in segment_results
                ),
            }
        )
    return summaries


def analyze_batch(applications: list[dict], results: list[dict]) -> dict:
    """Return portfolio-level metrics and useful applicant segments."""
    probabilities = [
        _probability_as_percent(
            result.get("default_probability", result.get("Probability of default", 0))
        )
        for result in results
    ]
    decisions = Counter(result.get("decision", result.get("Final Decision", "Unknown")) for result in results)
    tiers = Counter(result.get("risk_tier", result.get("Risk Tier", "Unknown")) for result in results)
    total = len(results)
    denominator = total or 1

    ranked_rows = sorted(
        (
            {
                "row": index + 1,
                "probability_of_default": round(probability, 2),
                "decision": result.get("decision", result.get("Final Decision", "Unknown")),
                "risk_tier": result.get("risk_tier", result.get("Risk Tier", "Unknown")),
                "occupation": application.get("OCCUPATION", "Unknown"),
            }
            for index, (application, result, probability) in enumerate(
                zip(applications, results, probabilities)
            )
        ),
        key=lambda item: item["probability_of_default"],
        reverse=True,
    )

    return {
        "total_applicants": total,
        "decision_counts": dict(decisions),
        "risk_tier_counts": dict(tiers),
        "approval_rate": round(decisions["Auto Approve"] / denominator * 100, 2),
        "review_rate": round(decisions["Manual Review Required"] / denominator * 100, 2),
        "decline_rate": round(decisions["Auto Decline"] / denominator * 100, 2),
        "average_default_probability": round(mean(probabilities), 2) if probabilities else 0,
        "median_default_probability": round(median(probabilities), 2) if probabilities else 0,
        "highest_default_probability": round(max(probabilities), 2) if probabilities else 0,
        "top_risk_rows": ranked_rows[:5],
        "by_occupation": _segment_summary(applications, results, "OCCUPATION"),
        "by_contract_type": _segment_summary(applications, results, "CONTRACT_TYPE"),
        "by_credit_history": _segment_summary(applications, results, "CREDIT_HISTORY"),
    }


def analyze_uploaded_data(dataframe: pd.DataFrame) -> dict:
    """Create descriptive analytics without running the prediction model."""
    numeric = dataframe.select_dtypes(include="number")
    numeric_summary = []
    for column in numeric.columns:
        values = numeric[column].dropna()
        numeric_summary.append(
            {
                "field": column,
                "average": round(float(values.mean()), 2) if not values.empty else None,
                "minimum": round(float(values.min()), 2) if not values.empty else None,
                "q1": round(float(values.quantile(0.25)), 2) if not values.empty else None,
                "median": round(float(values.median()), 2) if not values.empty else None,
                "q3": round(float(values.quantile(0.75)), 2) if not values.empty else None,
                "maximum": round(float(values.max()), 2) if not values.empty else None,
            }
        )

    categorical_summary = []
    for column in dataframe.select_dtypes(exclude="number").columns:
        counts = dataframe[column].fillna("Missing").astype(str).value_counts()
        categorical_summary.append(
            {
                "field": column,
                "unique_values": int(counts.size),
                "top_values": [
                    {"value": str(value), "count": int(count)}
                    for value, count in counts.head(5).items()
                ],
            }
        )

    missing = [
        {"field": column, "missing": int(count)}
        for column, count in dataframe.isna().sum().items()
        if count
    ]
    return {
        "rows": int(len(dataframe)),
        "columns": int(len(dataframe.columns)),
        "fields": [str(column) for column in dataframe.columns],
        "numeric_summary": numeric_summary,
        "categorical_summary": categorical_summary,
        "missing_values": missing,
    }


def _histogram(values: list[float], bins: int = 10) -> dict:
    if not values:
        return {"bins": [], "counts": []}
    lower, upper = float(min(values)), float(max(values))
    if lower == upper:
        lower -= 0.5
        upper += 0.5
    edges = np.linspace(lower, upper, min(bins, max(1, len(set(values)))) + 1)
    counts, edges = np.histogram(values, bins=edges)
    return {
        "bins": [f"{left:.2f}–{right:.2f}" for left, right in zip(edges[:-1], edges[1:])],
        "counts": [int(count) for count in counts],
    }


def _correlation_matrix(dataframe: pd.DataFrame) -> dict:
    preferred = [
        "TOTAL_INCOME", "CREDIT_AMOUNT", "ANNUAL_LOAN_PAYMENT",
        "GOODS_PRICE", "AGE", "CREDIT_SCORE", "CREDIT_HISTORY",
    ]
    fields = [field for field in preferred if field in dataframe.columns]
    matrix = dataframe[fields].corr().round(3).fillna(0)
    return {"fields": fields, "values": matrix.values.tolist()}


def _risk_bucket(probability: float) -> str:
    if probability < 8:
        return "Low Risk"
    if probability < 20:
        return "Manual Review"
    return "High Risk"


def _risk_overview(probabilities: list[float]) -> dict:
    counts = Counter(_risk_bucket(value) for value in probabilities)
    total = len(probabilities)
    colors = {"Low Risk": "low", "Manual Review": "review", "High Risk": "high"}
    return {
        "total": total,
        "segments": [
            {
                "label": label,
                "count": counts[label],
                "percentage": round(counts[label] / total * 100, 1) if total else 0,
                "tone": colors[label],
            }
            for label in ("Low Risk", "Manual Review", "High Risk")
        ],
    }


def _risk_by_numeric_field(applications: list[dict], probabilities: list[float], field: str) -> dict | None:
    values = []
    for application, probability in zip(applications, probabilities):
        try:
            value = float(application.get(field))
        except (TypeError, ValueError):
            continue
        values.append((value, probability))
    if not values:
        return None
    series = pd.Series([value for value, _ in values])
    if series.nunique() < 2:
        return None
    labels = ["Low", "Medium", "High"]
    try:
        groups = pd.qcut(series, q=3, labels=labels, duplicates="drop")
    except ValueError:
        return None
    buckets = defaultdict(list)
    for group, (_, probability) in zip(groups.astype(str), values):
        buckets[group].append(probability)
    return {
        "field": field,
        "groups": [
            {
                "label": label,
                "applicants": len(buckets[label]),
                "average_default_probability": round(mean(buckets[label]), 2),
            }
            for label in labels
            if buckets[label]
        ],
    }


def _psi(current: pd.Series, baseline: pd.Series, bins: int = 10) -> float | None:
    current = pd.to_numeric(current, errors="coerce").dropna()
    baseline = pd.to_numeric(baseline, errors="coerce").dropna()
    if current.empty or baseline.empty:
        return None
    edges = sorted(set(baseline.quantile([i / bins for i in range(bins + 1)]).tolist()))
    if len(edges) < 3:
        return 0.0
    edges[0] -= 1e-9
    edges[-1] += 1e-9
    expected = pd.cut(baseline, bins=edges, include_lowest=True).value_counts(normalize=True)
    actual = pd.cut(current, bins=edges, include_lowest=True).value_counts(normalize=True)
    expected, actual = expected.align(actual, fill_value=0.0)
    expected = expected.clip(lower=1e-6)
    actual = actual.clip(lower=1e-6)
    return round(float(((actual - expected) * (actual / expected).map(math.log)).sum()), 4)


def analyze_scored_batch(
    dataframe: pd.DataFrame,
    applications: list[dict],
    results: list[dict],
    baseline: pd.DataFrame | None = None,
) -> dict:
    """Build chart-ready distribution, target, explanation, and drift analytics."""
    analysis = analyze_uploaded_data(dataframe)
    probabilities = [
        float(result.get("default_probability", 0.0)) * 100 for result in results
    ]
    analysis.update(analyze_batch(applications, results))
    analysis["risk_probability_histogram"] = _histogram(probabilities)
    credit_values = pd.to_numeric(dataframe.get("CREDIT_AMOUNT", pd.Series(dtype=float)), errors="coerce").dropna().tolist()
    analysis["credit_amount_histogram"] = _histogram([float(value) for value in credit_values])
    analysis["correlations"] = _correlation_matrix(dataframe)
    analysis["risk_overview"] = _risk_overview(probabilities)
    analysis["risk_by_feature"] = [
        profile for field in ("CREDIT_SCORE", "CREDIT_TO_INCOME", "CREDIT_AMOUNT", "TOTAL_INCOME", "AGE")
        if (profile := _risk_by_numeric_field(applications, probabilities, field)) is not None
    ]

    segment_field = "OCCUPATION" if "OCCUPATION" in dataframe.columns else None
    segment_target = []
    if segment_field:
        grouped = defaultdict(list)
        for application, probability in zip(applications, probabilities):
            grouped[str(application.get(segment_field, "Unknown"))].append(probability)
        segment_target = [
            {
                "segment": segment,
                "applicants": len(values),
                "average_default_probability": round(mean(values), 2),
            }
            for segment, values in sorted(grouped.items())
        ]
    analysis["target_by_segment"] = {
        "field": segment_field,
        "metric": "average_predicted_default_probability",
        "segments": segment_target,
    }
    analysis["segment_analysis"] = {
        field: sorted(_segment_summary(applications, results, field),
                      key=lambda item: item["average_default_probability"], reverse=True)
        for field in ("OCCUPATION", "CONTRACT_TYPE", "QUALIFICATION", "FAMILY_STATUS")
        if field in dataframe.columns
    }

    shap_totals = defaultdict(float)
    shap_labels = {}
    for result in results:
        for item in result.get("shap_values", result.get("explanations", {}).get("positive", []) + result.get("explanations", {}).get("negative", [])):
            shap_totals[item["feature"]] += abs(float(item["impact"]))
            shap_labels[item["feature"]] = item["label"]
    top_global = sorted(shap_totals.items(), key=lambda item: item[1], reverse=True)[:10]
    analysis["global_shap"] = [
        {"feature": feature, "label": shap_labels[feature], "mean_abs_impact": round(total / max(len(results), 1), 4)}
        for feature, total in top_global
    ]

    baseline = baseline if baseline is not None else pd.DataFrame()
    drift = []
    for field in ("TOTAL_INCOME", "CREDIT_AMOUNT", "ANNUAL_LOAN_PAYMENT", "CREDIT_SCORE"):
        if field in dataframe.columns and field in baseline.columns:
            psi = _psi(dataframe[field], baseline[field])
            drift.append({
                "field": field,
                "psi": psi,
                "status": "warning" if psi is not None and psi >= 0.2 else "stable",
                "message": "Meaningful distribution shift detected." if psi is not None and psi >= 0.2 else "No material shift detected.",
            })
    analysis["drift"] = drift
    correlation_pairs = []
    fields = analysis["correlations"]["fields"]
    matrix = analysis["correlations"]["values"]
    for row in range(len(fields)):
        for column in range(row + 1, len(fields)):
            correlation_pairs.append({
                "left": fields[row],
                "right": fields[column],
                "value": matrix[row][column],
            })
    analysis["strongest_correlations"] = sorted(
        correlation_pairs, key=lambda item: abs(item["value"]), reverse=True
    )[:5]
    analysis["data_health"] = {
        "missing_cells": int(dataframe.isna().sum().sum()),
        "duplicate_rows": int(dataframe.duplicated().sum()),
        "valid_rows": len(results),
        "total_rows": len(dataframe),
        "validation_status": "good" if len(results) == len(dataframe) else "warning",
        "range_status": "not_available",
        "category_status": "not_available",
        "drift_status": (
            "warning" if any(item["status"] == "warning" for item in drift)
            else "good" if drift else "not_available"
        ),
    }
    analysis["preprocessing"] = {
        "missing_value_policy": "Rows with missing or invalid model fields are retained for review and excluded from assessment.",
        "numeric_policy": "Model preprocessing applies trained clipping and feature transformations before inference.",
    }
    return analysis
