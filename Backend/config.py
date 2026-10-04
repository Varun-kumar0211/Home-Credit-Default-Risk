import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "Frontend"
DATASET_DB_PATH = Path(
    os.environ.get("CREDIT_DATASET_DB", PROJECT_ROOT / "Data" / "datasets.sqlite3")
)
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL.removeprefix("postgres://")
ENVIRONMENT = os.environ.get("APP_ENV", "development").lower()

if ENVIRONMENT == "production":
    missing = [
        name
        for name in ("CREDIT_JWT_SECRET", "CREDIT_AUTH_USERNAME", "CREDIT_AUTH_PASSWORD")
        if not os.environ.get(name)
    ]
    if missing:
        raise RuntimeError(f"Missing required production settings: {', '.join(missing)}")

JWT_SECRET = os.environ.get("CREDIT_JWT_SECRET", "local-development-secret-change-me")
AUTH_USERNAME = os.environ.get("CREDIT_AUTH_USERNAME", "analyst")
AUTH_PASSWORD = os.environ.get("CREDIT_AUTH_PASSWORD", "credit-risk-demo")
