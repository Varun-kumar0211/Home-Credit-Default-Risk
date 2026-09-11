"""Train a calibrated model bundle for the Home Credit API.

Run from the repository root after placing application_train.csv in Data/:
    python "data cleaning/train_calibrated_model.py"
"""

import argparse
import sys
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from Backend.model_bundle import CalibratedModelBundle

CATEGORICAL_COLUMNS = [
    "NAME_EDUCATION_TYPE", "OCCUPATION_TYPE", "NAME_CONTRACT_TYPE",
]
NUMERIC_COLUMNS = [
    "AMT_INCOME_TOTAL", "AMT_CREDIT", "AMT_ANNUITY", "AMT_GOODS_PRICE",
    "AGE_YEARS", "YEARS_EMPLOYED", "CREDIT_SCORE", "NO_CREDIT_HISTORY",
    "CREDIT_TO_INCOME_RATIO", "ANNUITY_TO_INCOME_RATIO", "CREDIT_TERM",
    "GOODS_TO_CREDIT_RATIO", "EMPLOYED_TO_BIRTH_RATIO",
]
LOG_COLUMNS = ["LOG_INCOME", "LOG_CREDIT", "LOG_GOODS_PRICE", "LOG_ANNUITY"]
FEATURE_COLUMNS = CATEGORICAL_COLUMNS + NUMERIC_COLUMNS + LOG_COLUMNS
CLIPPED_NUMERIC_COLUMNS = NUMERIC_COLUMNS + LOG_COLUMNS


def build_features(data: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    if "TARGET" not in data.columns:
        raise ValueError(
            "Training data must contain TARGET labels. "
            "application_test.csv is unlabeled and can only be used for inference."
        )

    required = {
        "TARGET", "DAYS_BIRTH", "DAYS_EMPLOYED", "CODE_GENDER",
        "NAME_EDUCATION_TYPE", "NAME_FAMILY_STATUS", "OCCUPATION_TYPE",
        "NAME_CONTRACT_TYPE", "AMT_INCOME_TOTAL", "AMT_CREDIT",
        "AMT_ANNUITY", "AMT_GOODS_PRICE", "EXT_SOURCE_1", "EXT_SOURCE_2",
        "EXT_SOURCE_3",
    }
    missing = sorted(required - set(data.columns))
    if missing:
        raise ValueError(f"Training data is missing columns: {', '.join(missing)}")

    features = pd.DataFrame(index=data.index)
    features["CODE_GENDER"] = data["CODE_GENDER"].fillna("X")
    features["NAME_EDUCATION_TYPE"] = data["NAME_EDUCATION_TYPE"].fillna("Unknown")
    features["NAME_FAMILY_STATUS"] = data["NAME_FAMILY_STATUS"].fillna("Unknown")
    features["OCCUPATION_TYPE"] = data["OCCUPATION_TYPE"].fillna("Unknown")
    features["NAME_CONTRACT_TYPE"] = data["NAME_CONTRACT_TYPE"].fillna("Cash loans")

    features["AMT_INCOME_TOTAL"] = data["AMT_INCOME_TOTAL"]
    features["AMT_CREDIT"] = data["AMT_CREDIT"]
    features["AMT_ANNUITY"] = data["AMT_ANNUITY"]
    features["AMT_GOODS_PRICE"] = data["AMT_GOODS_PRICE"]
    features["AGE_YEARS"] = data["DAYS_BIRTH"].abs() / 365
    features["YEARS_EMPLOYED"] = data["DAYS_EMPLOYED"].abs() / 365

    external_score = data[["EXT_SOURCE_1", "EXT_SOURCE_2", "EXT_SOURCE_3"]].mean(axis=1)
    features["CREDIT_SCORE"] = (600 * external_score) + 300
    features["NO_CREDIT_HISTORY"] = features["CREDIT_SCORE"].isna().astype("int8")
    features["CREDIT_SCORE"] = features["CREDIT_SCORE"].fillna(575)

    income = features["AMT_INCOME_TOTAL"].clip(lower=1)
    credit = features["AMT_CREDIT"].clip(lower=1)
    annuity = features["AMT_ANNUITY"].clip(lower=1)
    age = features["AGE_YEARS"].clip(lower=1)
    features["CREDIT_TO_INCOME_RATIO"] = credit / income
    features["ANNUITY_TO_INCOME_RATIO"] = annuity / income
    features["CREDIT_TERM"] = credit / annuity
    features["GOODS_TO_CREDIT_RATIO"] = features["AMT_GOODS_PRICE"] / credit
    features["EMPLOYED_TO_BIRTH_RATIO"] = features["YEARS_EMPLOYED"] / age

    features["LOG_INCOME"] = np.log1p(features["AMT_INCOME_TOTAL"].clip(lower=0))
    features["LOG_CREDIT"] = np.log1p(features["AMT_CREDIT"].clip(lower=0))
    features["LOG_GOODS_PRICE"] = np.log1p(features["AMT_GOODS_PRICE"].clip(lower=0))
    features["LOG_ANNUITY"] = np.log1p(features["AMT_ANNUITY"].clip(lower=0))

    for column in CATEGORICAL_COLUMNS:
        features[column] = features[column].astype("category")
    features = features[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)
    return features, data["TARGET"].astype("int8")


def make_calibrator(model, method):
    try:
        from sklearn.frozen import FrozenEstimator

        return CalibratedClassifierCV(FrozenEstimator(model), method=method)
    except ImportError:
        pass

    try:
        return CalibratedClassifierCV(estimator=model, method=method, cv="prefit")
    except TypeError:
        return CalibratedClassifierCV(base_estimator=model, method=method, cv="prefit")


def fit_clip_bounds(features: pd.DataFrame) -> dict:
    """Fit robust training-only bounds for numeric inference features."""
    return {
        column: (
            float(features[column].quantile(0.005)),
            float(features[column].quantile(0.995)),
        )
        for column in CLIPPED_NUMERIC_COLUMNS
    }


def apply_clip_bounds(features: pd.DataFrame, bounds: dict) -> pd.DataFrame:
    clipped = features.copy()
    for column, (lower, upper) in bounds.items():
        clipped[column] = clipped[column].clip(lower=lower, upper=upper)
    return clipped


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("Data/application_train.csv"))
    parser.add_argument("--output", type=Path, default=Path("data cleaning/calibrated_model_bundle.pkl"))
    parser.add_argument("--method", choices=("sigmoid", "isotonic"), default="sigmoid")
    parser.add_argument("--approve-threshold", type=float, default=0.08)
    parser.add_argument("--decline-threshold", type=float, default=0.20)
    args = parser.parse_args()

    if not 0 < args.approve_threshold < args.decline_threshold < 1:
        parser.error("thresholds must satisfy 0 < approve < decline < 1")

    data = pd.read_csv(args.input)
    features, target = build_features(data)
    x_train, x_remaining, y_train, y_remaining = train_test_split(
        features, target, test_size=0.30, stratify=target, random_state=42
    )
    x_calibration, x_test, y_calibration, y_test = train_test_split(
        x_remaining, y_remaining, test_size=0.50, stratify=y_remaining, random_state=42
    )
    clip_bounds = fit_clip_bounds(x_train)
    x_train = apply_clip_bounds(x_train, clip_bounds)
    x_calibration = apply_clip_bounds(x_calibration, clip_bounds)
    x_test = apply_clip_bounds(x_test, clip_bounds)

    model = lgb.LGBMClassifier(
        objective="binary", n_estimators=250, learning_rate=0.05,
        num_leaves=48, max_depth=8, min_child_samples=100,
        colsample_bytree=0.75, subsample=0.8, reg_alpha=0.1, reg_lambda=1.0,
        class_weight="balanced", random_state=42, verbosity=-1,
    )
    model.fit(x_train, y_train, categorical_feature=CATEGORICAL_COLUMNS)

    calibrator = make_calibrator(model, args.method)
    calibrator.fit(x_calibration, y_calibration)
    raw_probability = model.predict_proba(x_test)[:, 1]
    calibrated_probability = calibrator.predict_proba(x_test)[:, 1]

    print(f"raw ROC-AUC: {roc_auc_score(y_test, raw_probability):.4f}")
    print(f"calibrated ROC-AUC: {roc_auc_score(y_test, calibrated_probability):.4f}")
    print(f"raw Brier score: {brier_score_loss(y_test, raw_probability):.4f}")
    print(f"calibrated Brier score: {brier_score_loss(y_test, calibrated_probability):.4f}")

    importance = pd.Series(model.feature_importances_, index=FEATURE_COLUMNS)
    categorical_share = importance[CATEGORICAL_COLUMNS].sum() / importance.sum()
    numeric_share = importance[NUMERIC_COLUMNS + LOG_COLUMNS].sum() / importance.sum()
    print(f"categorical importance share: {categorical_share:.2%}")
    print(f"numeric importance share: {numeric_share:.2%}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    category_levels = {
        column: features[column].cat.categories.tolist()
        for column in CATEGORICAL_COLUMNS
    }
    joblib.dump(
        CalibratedModelBundle(
            calibrator, model, FEATURE_COLUMNS, category_levels, clip_bounds,
            args.approve_threshold, args.decline_threshold,
        ),
        args.output,
    )
    print(f"saved calibrated model: {args.output}")


if __name__ == "__main__":
    main()
