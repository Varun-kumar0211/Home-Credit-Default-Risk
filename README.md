# Home Credit Default Risk

A FastAPI credit-risk application with a custom HTML/CSS/JavaScript dashboard.
The application supports individual assessments, demo portfolio analysis, and
authenticated CSV uploads with applicant-level review.

Live deployment: https://home-credit-default-risk-zkv2.onrender.com

## Features

- JWT-authenticated analyst workspace
- Manual applicant assessment with labeled inputs
- Default three-applicant demo dataset
- CSV upload with row-level validation before storage
- Persistent SQLite dataset repository
- Stable unique applicant IDs
- Separate assessment endpoint for each applicant
- Numeric prediction responses:
  - Default probability
  - Risk and trust scores
  - Confidence
  - Decision and risk tier
- SHAP explanations for individual assessments
- Batch portfolio analytics:
  - Default-risk probability histogram
  - Credit-amount distribution
  - Predicted default rate by occupation
  - Numeric quartile and box-plot-style profiles
  - Financial correlation matrix
  - Global SHAP feature importance
  - PSI-based data-drift warnings
  - Missing-value and preprocessing policy information

## Run locally

Install the dependencies:

```bash
pip install -r requirements.txt
```

Start the application from the repository root:

```bash
uvicorn Backend.main:app --host 0.0.0.0 --port 8000
```

Open the dashboard at http://localhost:8000.
FastAPI documentation is available at http://localhost:8000/docs.

The frontend is served directly by FastAPI; no separate Gradio or frontend
server is required.

## Run with Docker Compose

From the project root:

```bash
docker compose up --build
```

Open:

- Dashboard: http://localhost:8000
- API documentation: http://localhost:8000/docs

## Dashboard workflows

The dashboard is organized into three pages:

1. **Manual assessment** — enter one applicant's demographic and financial
   details, then view the model decision and top positive and negative SHAP
   contributors.
2. **Default dataset** — inspect the bundled demo applicants and run an
   assessment for any applicant.
3. **Upload CSV** — upload a portfolio, review data quality and portfolio
   analytics, then assess individual valid applicants.

Uploaded rows are validated before they are stored. Invalid rows remain visible
with their validation messages, but are excluded from model assessment.

## Authentication

The dashboard uses JWT bearer authentication. Local development defaults are:

```text
Username: analyst
Password: credit-risk-demo
```

Configure credentials and the signing secret through environment variables:

```text
CREDIT_AUTH_USERNAME
CREDIT_AUTH_PASSWORD
CREDIT_JWT_SECRET
```

Production deployments must provide all three values. Do not use the local
development credentials or a development JWT secret in production.

## API endpoints

All `/api` endpoints require an `Authorization: Bearer <token>` header.

- `POST /auth/login` — exchange credentials for a JWT access token.
- `POST /api/manual-assessment` — validate and assess one applicant.
- `GET /api/default-data` — return the bundled demo dataset and analytics.
- `POST /api/default-data/applicants/{applicant_id}/predict` — assess one demo
  applicant.
- `POST /api/csv-analysis` — upload a CSV and return analytics and applicant
  records.
- `POST /api/upload-csv` — compatibility alias for CSV analysis.
- `POST /api/datasets/{dataset_id}/applicants/{applicant_id}/predict` — assess
  one uploaded applicant.

Legacy batch endpoints remain available:

- `POST /predict` — single-applicant prediction.
- `POST /predict_batch` — row-level batch predictions.
- `POST /predict_batch_analysis` — batch predictions with portfolio analytics.

## Frontend structure

- `Frontend/index.html` — dashboard shell and three workflow pages
- `Frontend/style.css` — shared responsive theme and dashboard components
- `Frontend/js/api.js` — authenticated API client
- `Frontend/js/main.js` — login, navigation, upload, and form event wiring
- `Frontend/js/views.js` — workflow orchestration
- `Frontend/js/components.js` — reusable rendering functions

The dashboard uses the shared teal/slate visual theme across authentication,
forms, analytics cards, tables, charts, and assessment dialogs.

## Data and persistence

Uploaded datasets are stored in the SQLite database configured by
`DATASET_DB_PATH`. Dataset records are scoped to the authenticated owner and
are retained according to the configured retention policy.

The model accepts the applicant fields defined by
`Backend/schemas.py`. Refer to that schema for supported categories, numeric
ranges, and business validation rules.

## Retrain with calibration

Place the original `application_train.csv` in `Data/`, then run from the project root:

```bash
python "data cleaning/train_calibrated_model.py"
```

The script creates `data cleaning/calibrated_model_bundle.pkl`. It adds log-transformed
monetary features, fits transformations only from the training split, calibrates
probabilities with Platt scaling by default, and reports ROC-AUC and Brier score before
and after calibration. Use `--method isotonic` when the calibration split is large
enough for a non-parametric calibrator.

The API automatically uses the calibrated bundle when it exists and otherwise
falls back to the existing `LGB_CLASSIFIER_MODEL.pkl` artifact.

## Testing and validation

Run the available test suite from the repository root:

```bash
python -m pytest -q
```

For frontend syntax checks:

```bash
node --check Frontend/js/api.js
node --check Frontend/js/components.js
node --check Frontend/js/views.js
node --check Frontend/js/main.js
```
