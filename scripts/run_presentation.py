"""Local classroom setup: python scripts/run_presentation.py [--port 8001].

Creates a separate fictional SQLite database once; subsequent starts preserve it.
These intentionally simple credentials are for localhost presentations only.
"""
import argparse
import os
from pathlib import Path
import sys

PRESENTATION_PASSWORDS = {
    "applicant": "applicant",
    "analyst": "analyst",
    "reviewer": "reviewer",
    "admin": "admin",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    path = root / "Data" / "presentation.sqlite3"
    # Explicitly local, regardless of inherited cloud configuration.
    os.environ.update(APP_ENV="development", DATABASE_URL="", CREDIT_DATASET_DB=str(path),
                      CREDIT_AUTH_USERNAME="analyst", CREDIT_AUTH_PASSWORD=PRESENTATION_PASSWORDS["analyst"],
                      DEMO_ENABLED="false", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "Backend"))
    from Backend.auth import hash_password
    from Backend.repository import DatasetRepository
    from Backend.demo import initialize_demo
    from Backend.Cleaning import model_id, artifact_hash

    if not path.exists():
        repo = DatasetRepository(path)
        for role, password in PRESENTATION_PASSWORDS.items():
            repo.create_user(role, hash_password(password), role)
        initialize_demo(repo, hash_password, model_id, artifact_hash,
                        accounts={role: role for role in PRESENTATION_PASSWORDS})
        print("Created fictional presentation accounts and two sample applications.")
    print(f"Presentation workspace: http://127.0.0.1:{args.port}/")
    import uvicorn
    uvicorn.run("Backend.main:app", host="127.0.0.1", port=args.port, workers=1, limit_concurrency=8)


if __name__ == "__main__":
    main()
