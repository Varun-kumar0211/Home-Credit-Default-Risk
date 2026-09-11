import pandas as pd 
import joblib
import shap
import numpy as np
import os

current_dir = os.path.dirname(__file__)
model_path = os.path.abspath(os.path.join(current_dir, '..', 'data cleaning', 'LGB_CLASSIFIER_MODEL.pkl'))
calibrated_model_path = os.path.abspath(
    os.path.join(current_dir, '..', 'data cleaning', 'calibrated_model_bundle.pkl')
)

artifact_path = calibrated_model_path if os.path.exists(calibrated_model_path) else model_path
artifact = joblib.load(artifact_path)
prediction_model = artifact.predictor if hasattr(artifact, 'predictor') else artifact
explanation_model = (
    artifact.explanation_model
    if hasattr(artifact, 'explanation_model')
    else prediction_model
)
feature_columns = getattr(artifact, 'feature_columns', None)
category_levels = getattr(artifact, 'category_levels', {})
clip_bounds = getattr(artifact, 'clip_bounds', {})
approve_threshold = getattr(artifact, 'approve_threshold', 0.08)
decline_threshold = getattr(artifact, 'decline_threshold', 0.20)
explainer=shap.TreeExplainer(explanation_model)


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0 or not np.isfinite(denominator):
        return 0.0
    return numerator / denominator


def _normalize_threshold(value: float, fallback: float) -> float:
    """Accept either a fraction (0.08) or UI percentage (8)."""
    if value is None:
        return fallback
    normalized = float(value)
    if normalized > 1:
        normalized /= 100
    if not np.isfinite(normalized) or not 0 <= normalized <= 1:
        raise ValueError("Decision thresholds must be between 0 and 1 or 0% and 100%")
    return normalized


def process_application(raw_data)->dict:
    request_approve_threshold = _normalize_threshold(
        raw_data.get('APPROVE_THRESHOLD'), approve_threshold
    )
    request_decline_threshold = _normalize_threshold(
        raw_data.get('DECLINE_THRESHOLD'), decline_threshold
    )
    if request_approve_threshold >= request_decline_threshold:
        raise ValueError("APPROVE_THRESHOLD must be lower than DECLINE_THRESHOLD")
    ml_feature={}
    ml_feature['CODE_GENDER']=raw_data['GENDER']
    ml_feature['NAME_EDUCATION_TYPE']=raw_data['QUALIFICATION']
    ml_feature['NAME_FAMILY_STATUS']=raw_data['FAMILY_STATUS']
    ml_feature['OCCUPATION_TYPE']=raw_data['OCCUPATION']
    ml_feature['NAME_CONTRACT_TYPE']=raw_data['CONTRACT_TYPE']
    ml_feature['AMT_INCOME_TOTAL']=raw_data['TOTAL_INCOME']
    ml_feature['AMT_CREDIT']=raw_data['CREDIT_AMOUNT']
    ml_feature['AMT_ANNUITY']=raw_data['ANNUAL_LOAN_PAYMENT']
    ml_feature['AMT_GOODS_PRICE']=raw_data['GOODS_PRICE']
    ml_feature['AGE_YEARS']=raw_data['AGE']
    ml_feature['YEARS_EMPLOYED']=raw_data['YEARS_OF_EXPERIENCE']
    ml_feature['CREDIT_SCORE']=raw_data['CREDIT_SCORE'] 
    ml_feature['NO_CREDIT_HISTORY']=raw_data['CREDIT_HISTORY']    
    ml_feature['CREDIT_TO_INCOME_RATIO']=_safe_ratio(
        raw_data['CREDIT_AMOUNT'], raw_data['TOTAL_INCOME']
    )
    ml_feature['ANNUITY_TO_INCOME_RATIO']=_safe_ratio(
        raw_data['ANNUAL_LOAN_PAYMENT'], raw_data['TOTAL_INCOME']
    )
    ml_feature['CREDIT_TERM']=_safe_ratio(
        raw_data['CREDIT_AMOUNT'], raw_data['ANNUAL_LOAN_PAYMENT']
    )
    ml_feature['GOODS_TO_CREDIT_RATIO']=_safe_ratio(
        raw_data['GOODS_PRICE'], raw_data['CREDIT_AMOUNT']
    )
    ml_feature['EMPLOYED_TO_BIRTH_RATIO']=_safe_ratio(
        raw_data['YEARS_OF_EXPERIENCE'], raw_data['AGE']
    )

    if feature_columns and 'LOG_INCOME' in feature_columns:
        ml_feature['LOG_INCOME'] = np.log1p(ml_feature['AMT_INCOME_TOTAL'])
        ml_feature['LOG_CREDIT'] = np.log1p(ml_feature['AMT_CREDIT'])
        ml_feature['LOG_GOODS_PRICE'] = np.log1p(ml_feature['AMT_GOODS_PRICE'])
        ml_feature['LOG_ANNUITY'] = np.log1p(ml_feature['AMT_ANNUITY'])

    df=pd.DataFrame([ml_feature])
    if feature_columns:
        df = df.reindex(columns=feature_columns)
    for column, bounds in clip_bounds.items():
        if column in df:
            df[column] = df[column].clip(lower=bounds[0], upper=bounds[1])
    categorical_cols = ['CODE_GENDER', 'NAME_EDUCATION_TYPE', 'NAME_FAMILY_STATUS', 'OCCUPATION_TYPE', 'NAME_CONTRACT_TYPE']
    for col in categorical_cols:
        if col not in df:
            continue
        if col in category_levels:
            df[col] = pd.Categorical(df[col], categories=category_levels[col])
        else:
            df[col] = df[col].astype('category')

    raw_prob_array = prediction_model.predict_proba(df)
    prob_default = float(np.clip(raw_prob_array[0][1], 0.0, 1.0))
    default_prob_percentage = prob_default * 100
    
    shap_values=explainer(df)
    Person_shap_values=shap_values.values

    values_array = shap_values.values

    if len(values_array.shape) == 3:
        Person_shap_values = values_array[0, :, 1]
    elif len(values_array.shape) == 2:
        Person_shap_values = values_array[0]
    else:
        Person_shap_values = values_array
    
   

    shap_impacts=list(zip(df.columns,Person_shap_values))
    shap_impacts.sort(key=lambda x:x[1])

    strength = [(feat, round(val, 4)) for feat, val in shap_impacts[:3]]
    red_flag = [(feat, round(val, 4)) for feat, val in shap_impacts[-3:]]

    # Keep scores continuous; rounding before subtraction made tiny risks look
    # identical to a perfect 100/100 trust score.
    risk_score = default_prob_percentage
    trust_score = 100.0 - default_prob_percentage

    action=""
    tier=""
    interest_rate=""

    if prob_default <= request_approve_threshold:
        action="Auto Approve"
        tier="Tier A"
        interest_rate="5.5%"
    elif prob_default < request_decline_threshold:
        action="Manual Review Required"
        tier="Tier B"
        interest_rate="10.5%"
    else:
        action="Auto Decline"
        tier="Tier C"
        interest_rate="N/A"

    confidence = max(prob_default, 1.0 - prob_default) * 100
    
    monthly_income = ml_feature['AMT_INCOME_TOTAL']/12
    max_allowed_payment=monthly_income*0.36
    annuity=ml_feature['AMT_ANNUITY']
    req_monthly_payment=(annuity/12) if not np.isnan(annuity) else 0

    recommended_action=""

    if req_monthly_payment > max_allowed_payment:
        recommended_action=f"Counter Offer: ${max_allowed_payment:,.2f}/mo maximum"
    
    else:
        recommended_action="Approve Requested Amount"

    if action=="Auto Decline":
        recommended_action="Decline Risk Too High"
    
    return {
        "Final Decision":action,
        "Risk Tier":tier,
        "Probability of default":f"{default_prob_percentage:,.2f}%",
        "Trust Score":f"{trust_score:.2f}/100",
        "Risk Score":f"{risk_score:.2f}/100",
        "Model confidence":f"{round(confidence)}%",
        "Approve threshold":f"{request_approve_threshold * 100:.2f}%",
        "Decline threshold":f"{request_decline_threshold * 100:.2f}%",
        "Recommended Rate":interest_rate,
        "Loan Amount Decision":recommended_action,
        "Strength":strength,
        "Red Flag":red_flag



    }

