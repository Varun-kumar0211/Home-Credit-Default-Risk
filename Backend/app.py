"""System Risk Evaluation Desk — Gradio front-end for the scoring API."""

import os
import re
import time
from urllib.parse import urlparse

import gradio as gr
import requests
import pandas as pd
import numpy as np


API_SINGLE_URL = os.environ.get("API_SINGLE_URL", "http://127.0.0.1:8000/predict")
API_BATCH_URL = os.environ.get("API_BATCH_URL", "http://127.0.0.1:8000/predict_batch")


GENDER_OPTIONS = ["M", "F", "X"]
QUALIFICATION_OPTIONS = [
    "Secondary / secondary special",
    "Higher education",
    "Incomplete higher",
    "Lower secondary",
    "Academic degree",
]
FAMILY_STATUS_OPTIONS = [
    "Married",
    "Single / not married",
    "Civil marriage",
    "Separated",
    "Widow",
]
OCCUPATION_OPTIONS = [
    "Laborers", "Core staff", "Sales staff", "Managers", "Drivers",
    "High skill tech staff", "Accountants", "Medicine staff",
    "Security staff", "Cooking staff",
]
CONTRACT_TYPE_OPTIONS = ["Cash loans", "Revolving loans"]
CREDIT_HISTORY_OPTIONS = [0, 1]


COLOR_ACCENT = "#0f766e"
COLOR_ACCENT_DARK = "#115e59"
COLOR_GOOD = "#15803d"
COLOR_GOOD_BG = "#dcfce7"
COLOR_WARN = "#b45309"
COLOR_WARN_BG = "#fef3c7"
COLOR_BAD = "#b91c1c"
COLOR_BAD_BG = "#fee2e2"
COLOR_NEUTRAL = "#334155"
COLOR_NEUTRAL_BG = "#e2e8f0"


THEME = gr.themes.Base(
    primary_hue="teal",
    secondary_hue="slate",
    neutral_hue="slate",
).set(
    button_primary_background_fill=COLOR_ACCENT,
    button_primary_background_fill_hover=COLOR_ACCENT_DARK,
    block_title_text_weight="600",
    block_radius="12px",
    body_background_fill="#f8fafc",
)


CUSTOM_CSS = f"""
#app-header {{ text-align: center; margin-bottom: 0; font-weight: 700; }}
#app-subheader {{ text-align: center; color: #64748b; margin-bottom: 1.5rem; }}

/* The CSV drop zone, made to look intentional instead of a bare gray box */
.upload-zone {{
    border: 2px dashed #94a3b8 !important;
    border-radius: 14px !important;
    background: #f1f5f9 !important;
}}

footer.app-footer {{
    text-align: center;
    color: #94a3b8;
    font-size: 0.8rem;
    margin-top: 2rem;
}}
"""


def _extract_percent(value) -> float:
    """Normalize numeric/percent input to a 0-100 float."""
    is_percent_string = isinstance(value, str) and "%" in value

    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = re.search(r"[-+]?\d*\.?\d+", str(value))
        number = float(match.group()) if match else 0.0

    if 0 <= number <= 1:
        number *= 100

    return max(0.0, min(100.0, number))


def _status_from_keywords(text: str) -> str:
    """Map free-form status text to one of: good, warn, bad, neutral."""
    lowered = str(text).lower()
    if any(word in lowered for word in ("approve", "accept", "low")):
        return "good"
    if any(word in lowered for word in ("review", "medium", "pending", "moderate")):
        return "warn"
    if any(word in lowered for word in ("declin", "reject", "high", "fail", "default")):
        return "bad"
    return "neutral"


def _status_colors(status: str):
    """Return (text_color, background_color) for a status."""
    return {
        "good": (COLOR_GOOD, COLOR_GOOD_BG),
        "warn": (COLOR_WARN, COLOR_WARN_BG),
        "bad": (COLOR_BAD, COLOR_BAD_BG),
        "neutral": (COLOR_NEUTRAL, COLOR_NEUTRAL_BG),
    }[status]


def render_badge(label: str, value) -> str:
    """Render a small colored badge for a label/value pair."""
    status = _status_from_keywords(value)
    text_color, bg_color = _status_colors(status)
    return f"""
    <div style="margin: 0.35rem 0;">
        <span style="color:#64748b !important; font-size:0.85rem;">{label}</span><br/>
        <span style="display:inline-block; margin-top:2px; padding:4px 14px;
                     border-radius:999px; font-weight:600; font-size:0.95rem;
                     color:{text_color} !important; background:{bg_color} !important;">
            {value}
        </span>
    </div>
    """


def _format_display_value(raw_value, percent: float, style: str) -> str:
    """Format a value for display beside a gauge (percent or score)."""
    if isinstance(raw_value, str) and ("%" in raw_value or "/" in raw_value):
        match = re.search(r"[-+]?\d*\.?\d+", raw_value)
        raw_number = float(match.group()) if match else None
        if raw_number is None or raw_number > 1:
            return raw_value
    if style == "score":
        return f"{percent:.0f}/100"
    return f"{percent:.0f}%"


def render_gauge(label: str, raw_value, higher_is_riskier: bool, display_style: str = "percent") -> str:
    """Render an inline horizontal gauge for a numeric score."""
    percent = _extract_percent(raw_value)
    display_text = _format_display_value(raw_value, percent, display_style)

    if higher_is_riskier:
        status = "good" if percent <= 30 else "warn" if percent <= 60 else "bad"
    else:
        status = "bad" if percent <= 30 else "warn" if percent <= 60 else "good"

    bar_color, _ = _status_colors(status)

    return f"""
    <div style="margin: 0.6rem 0;">
        <div style="display:flex; justify-content:space-between; font-size:0.85rem;">
            <span style="color:#64748b !important;">{label}</span>
            <span style="font-weight:600; color:#0f172a !important;">{display_text}</span>
        </div>
        <div style="background:#e2e8f0 !important; border-radius:999px; height:10px; margin-top:4px;">
            <div style="width:{percent}%; background:{bar_color} !important; height:100%;
                        border-radius:999px;"></div>
        </div>
    </div>
    """


def _card(inner_html: str) -> str:
    """Wrap inner HTML in a self-contained light card."""
    return f"""
    <div style="background:#ffffff !important; color:#0f172a !important;
                border:1px solid #e2e8f0; border-radius:14px;
                padding:1.5rem 1.75rem; color-scheme: light;">
        {inner_html}
    </div>
    """


def render_result_dashboard(result: dict) -> str:
    """Build the HTML dashboard from a single API result dict."""
    strengths_html = "".join(
        f"""<li style="border-left:3px solid {COLOR_GOOD}; padding:2px 10px; margin:4px 0;
                       color:#0f172a !important;">
                <b style="color:#0f172a !important;">{feature}</b> — impact {value}</li>"""
        for feature, value in result["Strength"]
    )
    red_flags_html = "".join(
        f"""<li style="border-left:3px solid {COLOR_BAD}; padding:2px 10px; margin:4px 0;
                       color:#0f172a !important;">
                <b style="color:#0f172a !important;">{feature}</b> — impact +{value}</li>"""
        for feature, value in result["Red Flag"]
    )

    return _card(f"""
    <div style="display:flex; gap:2rem; flex-wrap:wrap; margin-bottom:0.5rem;">
        {render_badge("Final decision", result['Final Decision'])}
        {render_badge("Risk tier", result['Risk Tier'])}
    </div>

    {render_gauge("Probability of default", result['Probability of default'], higher_is_riskier=True, display_style="percent")}
    {render_gauge("Risk score", result['Risk Score'], higher_is_riskier=True, display_style="score")}
    {render_gauge("Trust score", result['Trust Score'], higher_is_riskier=False, display_style="score")}
    {render_gauge("Model confidence", result['Model confidence'], higher_is_riskier=False, display_style="percent")}

    <div style="display:flex; gap:2rem; flex-wrap:wrap; margin:1rem 0; font-size:0.9rem;">
        <div><span style="color:#64748b !important;">Recommended rate</span><br/>
             <b style="color:#0f172a !important;">{result['Recommended Rate']}</b></div>
        <div><span style="color:#64748b !important;">Loan amount decision</span><br/>
             <b style="color:#0f172a !important;">{result['Loan Amount Decision']}</b></div>
    </div>

    <hr style="border:none; border-top:1px solid #e2e8f0 !important; margin:1rem 0;"/>

    <div style="display:flex; gap:2rem; flex-wrap:wrap;">
        <div style="flex:1; min-width:220px;">
            <b style="color:{COLOR_GOOD} !important;">🛡️ Strengths</b>
            <ul style="list-style:none; padding-left:0;">{strengths_html}</ul>
        </div>
        <div style="flex:1; min-width:220px;">
            <b style="color:{COLOR_BAD} !important;">🚩 Red flags</b>
            <ul style="list-style:none; padding-left:0;">{red_flags_html}</ul>
        </div>
    </div>
    """)


def render_loading_message() -> str:
    """Simple loading card shown while the API call runs."""
    return _card("""
    <div style="text-align:center; padding:1.25rem; color:#64748b !important;">
        ⏳ Running the risk model, one moment…
    </div>
    """)


def render_error(title: str, detail: str) -> str:
    """Render a styled error card."""
    return _card(f"""
    <div style="border-left:4px solid {COLOR_BAD}; background:{COLOR_BAD_BG} !important;
                padding:0.75rem 1rem; border-radius:8px;">
        <b style="color:{COLOR_BAD} !important;">⚠️ {title}</b>
        <div style="margin-top:4px; font-size:0.9rem; color:#334155 !important;">{detail}</div>
    </div>
    """)


def build_form_payload(gender, qualification, family_status, occupation, contract_type,
                        income, credit_amount, annuity, goods_price, age,
                        experience_years, credit_score, credit_history) -> dict:
    """Create the JSON payload for the single-applicant endpoint."""
    return {
        "GENDER": gender,
        "QUALIFICATION": qualification,
        "FAMILY_STATUS": family_status,
        "OCCUPATION": occupation,
        "CONTRACT_TYPE": contract_type,
        "TOTAL_INCOME": income,
        "CREDIT_AMOUNT": credit_amount,
        "ANNUAL_LOAN_PAYMENT": annuity,
        "GOODS_PRICE": goods_price,
        "AGE": age,
        "YEARS_OF_EXPERIENCE": experience_years,
        "CREDIT_SCORE": credit_score,
        "CREDIT_HISTORY": credit_history,
    }


def score_single_applicant(payload: dict) -> str:
    """POST to the single prediction API and return HTML (or an error)."""
    try:
        response = requests.post(API_SINGLE_URL, json=payload)
    except requests.RequestException as error:
        return render_error("Connection failed", str(error))

    if response.status_code != 200:
        return render_error("Engine error", response.text)

    return render_result_dashboard(response.json())


def score_csv_batch(csv_file, selected_row_idx: str) -> str:
    """Call batch endpoint and render a selected row's dashboard."""
    try:
        applicants_df = pd.read_csv(csv_file).replace({np.nan: None})
        payload = applicants_df.to_dict(orient="records")
    except Exception as error:
        return render_error("File parsing error", str(error))

    try:
        response = requests.post(API_BATCH_URL, json=payload)
    except requests.RequestException as error:
        return render_error("Connection failed", str(error))

    if response.status_code != 200:
        return render_error("Engine error", response.text)

    batch_results = response.json()
    row_index = int(selected_row_idx) if selected_row_idx else 0
    if row_index >= len(batch_results):
        row_index = 0

    return render_result_dashboard(batch_results[row_index])


def show_loading_state():
    """Hide form and show loading placeholder before scoring."""
    return gr.update(visible=False), gr.update(visible=True), render_loading_message()


def handle_submission(gender, qualification, family_status, occupation, contract_type,
                       income, credit_amount, annuity, goods_price, age,
                       experience_years, credit_score, credit_history,
                       csv_file, selected_row_idx):
    """Perform scoring (single or batch) and return rendered HTML."""
    if csv_file is not None:
        report_html = score_csv_batch(csv_file, selected_row_idx)
    else:
        payload = build_form_payload(
            gender, qualification, family_status, occupation, contract_type,
            income, credit_amount, annuity, goods_price, age,
            experience_years, credit_score, credit_history,
        )
        report_html = score_single_applicant(payload)

    return report_html


def refresh_row_selector(csv_file):
    """Update row selector choices and show a brief CSV preview."""
    if csv_file is None:
        return gr.update(choices=[], value=None, visible=False), gr.update(value="", visible=False)

    dataframe = pd.read_csv(csv_file)
    choices = [str(i) for i in range(len(dataframe))]
    preview_text = (
        f"✅ Loaded **{len(dataframe)} rows** · "
        f"{len(dataframe.columns)} columns: {', '.join(dataframe.columns[:6])}"
        f"{' …' if len(dataframe.columns) > 6 else ''}"
    )
    return (
        gr.update(choices=choices, value="0", visible=True),
        gr.update(value=preview_text, visible=True),
    )


def go_back_to_input():
    """Switch the UI back to the input page."""
    return gr.update(visible=True), gr.update(visible=False)


with gr.Blocks(title="System Risk Evaluation Desk") as demo:

    gr.Markdown("# 🏦 System Risk Evaluation Desk", elem_id="app-header")
    gr.Markdown(
        "Score a single applicant, or upload a CSV to evaluate a whole batch.",
        elem_id="app-subheader",
    )

    with gr.Column(visible=True) as input_page:
        with gr.Tab("📝 Form input"):
            with gr.Group():
                gr.Markdown("#### Personal & employment details")
                with gr.Row():
                    gender = gr.Dropdown(GENDER_OPTIONS, label="Gender", value="M")
                    qualification = gr.Dropdown(
                        QUALIFICATION_OPTIONS, label="Qualification", value="Higher education"
                    )
                    family_status = gr.Dropdown(
                        FAMILY_STATUS_OPTIONS, label="Family status", value="Single / not married"
                    )
                with gr.Row():
                    occupation = gr.Dropdown(OCCUPATION_OPTIONS, label="Occupation", value="Core staff")
                    age = gr.Number(label="Age (years)", value=30)
                    experience_years = gr.Number(label="Years of experience", value=5)

            with gr.Group():
                gr.Markdown("#### Loan details")
                with gr.Row():
                    contract_type = gr.Dropdown(
                        CONTRACT_TYPE_OPTIONS, label="Contract type", value="Cash loans"
                    )
                    credit_amount = gr.Number(label="Credit amount requested", value=150000)
                    goods_price = gr.Number(label="Goods price", value=150000)
                with gr.Row():
                    income = gr.Number(label="Total income", value=50000)
                    annuity = gr.Number(label="Annual loan payment (annuity)", value=12000)

            with gr.Group():
                gr.Markdown("#### Credit profile")
                with gr.Row():
                    credit_score = gr.Number(label="Internal credit score (300–850)", value=710)
                    credit_history = gr.Dropdown(
                        CREDIT_HISTORY_OPTIONS,
                        label="Prior default on record? (0 = No, 1 = Yes)",
                        value=0,
                    )

        with gr.Tab("📊 CSV batch upload"):
            gr.Markdown(
                "Upload a CSV with one row per applicant. "
                "After upload, pick which row to inspect below."
            )
            file_uploader = gr.File(
                label="Evaluation matrix dataset (.csv)",
                file_types=[".csv"],
                elem_classes="upload-zone",
            )
            csv_preview = gr.Markdown(visible=False)
            row_selector = gr.Dropdown(choices=[], label="Row to inspect", visible=False)

            file_uploader.change(
                fn=refresh_row_selector,
                inputs=[file_uploader],
                outputs=[row_selector, csv_preview],
            )

        submit_action = gr.Button("🚀 Run risk assessment", variant="primary", size="lg")

    with gr.Column(visible=False) as output_page:
        result_box = gr.HTML()
        back_action = gr.Button("⬅️ Evaluate another applicant")

    gr.HTML('<footer class="app-footer">Internal decision-support tool · not a final credit decision</footer>')

    submit_action.click(
        fn=show_loading_state,
        inputs=[],
        outputs=[input_page, output_page, result_box],
    ).then(
        fn=handle_submission,
        inputs=[
            gender, qualification, family_status, occupation, contract_type,
            income, credit_amount, annuity, goods_price, age,
            experience_years, credit_score, credit_history,
            file_uploader, row_selector,
        ],
        outputs=[result_box],
    )

    back_action.click(fn=go_back_to_input, inputs=[], outputs=[input_page, output_page])


def wait_for_api(url: str, timeout: int = 30, interval: float = 1.0) -> None:
    """Block until the backend API responds successfully or timeout occurs."""
    parsed = urlparse(url)
    if not parsed.scheme:
        url = f"http://{url}"

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = requests.get(url, timeout=2)
            if response.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(interval)

    raise RuntimeError(f"API did not respond within {timeout} seconds: {url}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    api_health_url = os.environ.get("API_HEALTH_URL", "http://127.0.0.1:8000/health")
    print(f"Waiting for API at {api_health_url} before launching Gradio...")
    wait_for_api(api_health_url)
    demo.launch(server_name="0.0.0.0", server_port=port, theme=THEME, css=CUSTOM_CSS)
