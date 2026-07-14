import pandas as pd 
import joblib
import shap
import numpy as np
import os

current_dir = os.path.dirname(__file__)
model_path = os.path.abspath(os.path.join(current_dir, '..', 'data cleaning', 'LGB_CLASSIFIER_MODEL.pkl'))

model=joblib.load(model_path)
explainer=shap.TreeExplainer(model)

def process_application(raw_data)->dict:
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
    ml_feature['CREDIT_TO_INCOME_RATIO']=raw_data['CREDIT_AMOUNT']/raw_data['TOTAL_INCOME']
    ml_feature['ANNUITY_TO_INCOME_RATIO']=raw_data['ANNUAL_LOAN_PAYMENT']/raw_data['TOTAL_INCOME']
    ml_feature['CREDIT_TERM']=raw_data['CREDIT_AMOUNT']/raw_data['ANNUAL_LOAN_PAYMENT']
    ml_feature['GOODS_TO_CREDIT_RATIO']=raw_data['GOODS_PRICE']/raw_data['CREDIT_AMOUNT']
    ml_feature['EMPLOYED_TO_BIRTH_RATIO']=raw_data['YEARS_OF_EXPERIENCE']/raw_data['AGE']

    df=pd.DataFrame([ml_feature])
    categorical_cols = ['CODE_GENDER', 'NAME_EDUCATION_TYPE', 'NAME_FAMILY_STATUS', 'OCCUPATION_TYPE', 'NAME_CONTRACT_TYPE']
    for col in categorical_cols:
        df[col] = df[col].astype('category')

    raw_prob_array = model.predict_proba(df)
    prob_default = raw_prob_array[0][1] 
    default_prob_percentage = round(prob_default * 100)
    
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

    auto_approve_threshold=0.08
    review_threshold=0.15

    
    risk_score = default_prob_percentage
    trust_score = 100 - default_prob_percentage

    action=""
    tier=""
    interest_rate=""

    if prob_default <= auto_approve_threshold:
        action="Auto Approve"
        tier="Tier A"
        interest_rate="5.5%"
    elif prob_default <= review_threshold:
        action="Manual Review Required"
        tier="Tier B"
        interest_rate="10.5%"
    else:
        action="Auto Decline"
        tier="Tier C"
        interest_rate="N/A"

    confidence=0

    if prob_default < review_threshold:
        confidence=((review_threshold-prob_default)/review_threshold)*100
    else:
        confidence=((prob_default-review_threshold)/(1-review_threshold))*100
    
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
        "Probability of default":f"{prob_default:,.2f}%",
        "Trust Score":f"{trust_score}/100",
        "Risk Score":f"{risk_score}/100",
        "Model confidence":f"{round(confidence)}%",
        "Recommended Rate":interest_rate,
        "Loan Amount Decision":recommended_action,
        "Strength":strength,
        "Red Flag":red_flag



    }

