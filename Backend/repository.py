import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


class DatasetRepository:
    """Repository backed by SQLite locally or PostgreSQL when DATABASE_URL is set."""

    def __init__(self, path: Path, database_url: str | None = None, retention_hours: int = 24):
        self.path = path
        self.database_url = database_url
        self.retention_hours = retention_hours
        if not database_url:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @property
    def is_postgres(self):
        return bool(self.database_url)

    @contextmanager
    def _connection(self):
        if self.database_url:
            import psycopg

            with psycopg.connect(self.database_url) as connection:
                yield connection
        else:
            connection = sqlite3.connect(self.path)
            try:
                connection.execute("PRAGMA foreign_keys = ON")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _sql(self, query):
        return query.replace("?", "%s") if self.is_postgres else query

    def _executemany(self, connection, query, parameters):
        cursor = connection.cursor()
        try:
            cursor.executemany(self._sql(query), parameters)
        finally:
            cursor.close()

    def _table_columns(self, connection, table_name):
        if self.is_postgres:
            query = "SELECT column_name FROM information_schema.columns WHERE table_name = %s"
            rows = connection.execute(query, (table_name,)).fetchall()
            return {row[0] for row in rows}
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {row[1] for row in rows}

    def _record_migration(self, connection, migration_name):
        if self.is_postgres:
            connection.execute(
                self._sql("INSERT INTO schema_migrations (migration_name, applied_at) VALUES (?, ?) ON CONFLICT (migration_name) DO NOTHING"),
                (migration_name, datetime.now(timezone.utc).isoformat()),
            )
        else:
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations (migration_name, applied_at) VALUES (?, ?)",
                (migration_name, datetime.now(timezone.utc).isoformat()),
            )

    def _initialize(self):
        with self._connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (migration_name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            existing = self._table_columns(connection, "datasets")
            if not existing:
                connection.execute(
                    """CREATE TABLE datasets (
                        dataset_id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                        filename TEXT NOT NULL, created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL, analysis_json TEXT NOT NULL
                    )"""
                )
            if not self._table_columns(connection, "applicants"):
                connection.execute(
                    """CREATE TABLE applicants (
                        applicant_id TEXT NOT NULL, dataset_id TEXT NOT NULL,
                        row_number INTEGER NOT NULL, data_json TEXT NOT NULL,
                        validation_json TEXT NOT NULL,
                        PRIMARY KEY (dataset_id, applicant_id)
                    )"""
                )
            if not self._table_columns(connection, "users"):
                connection.execute(
                    """CREATE TABLE users (
                        username TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
                        created_at TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'analyst'
                    )"""
                )
            else:
                columns = self._table_columns(connection, "users")
                if "role" not in columns:
                    if self.is_postgres:
                        connection.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'analyst'")
                    else:
                        connection.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'analyst'")
            if not self._table_columns(connection, "current_applicants"):
                connection.execute(
                    """CREATE TABLE current_applicants (
                        applicant_id TEXT PRIMARY KEY, row_number INTEGER NOT NULL,
                        data_json TEXT NOT NULL, created_at TEXT NOT NULL
                    )"""
                )

            loan_schema = self._table_columns(connection, "loan_applications")
            if not loan_schema:
                connection.execute(
                    """CREATE TABLE loan_applications (
                        application_id TEXT PRIMARY KEY,
                        owner TEXT NOT NULL,
                        assigned_reviewer TEXT,
                        source_type TEXT NOT NULL,
                        dataset_id TEXT,
                        source_applicant_id TEXT,
                        applicant_snapshot TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )"""
                )
            elif "assigned_reviewer" not in loan_schema:
                if self.is_postgres:
                    connection.execute(
                        "ALTER TABLE loan_applications ADD COLUMN IF NOT EXISTS assigned_reviewer TEXT"
                    )
                else:
                    connection.execute(
                        "ALTER TABLE loan_applications ADD COLUMN assigned_reviewer TEXT"
                    )
            if not self._table_columns(connection, "model_versions"):
                connection.execute(
                    """CREATE TABLE model_versions (
                        model_id TEXT PRIMARY KEY,
                        model_name TEXT NOT NULL,
                        model_version TEXT NOT NULL,
                        artifact_hash TEXT,
                        model_source TEXT,
                        feature_schema TEXT,
                        preprocessing_version TEXT,
                        calibration_method TEXT,
                        created_at TEXT NOT NULL
                    )"""
                )
            if not self._table_columns(connection, "ml_predictions"):
                connection.execute(
                    """CREATE TABLE ml_predictions (
                        prediction_id TEXT PRIMARY KEY,
                        application_id TEXT NOT NULL REFERENCES loan_applications(application_id),
                        model_id TEXT NOT NULL REFERENCES model_versions(model_id),
                        default_probability REAL NOT NULL,
                        risk_score REAL NOT NULL,
                        trust_score REAL NOT NULL,
                        confidence REAL NOT NULL,
                        risk_tier TEXT,
                        model_decision TEXT,
                        approve_threshold REAL,
                        decline_threshold REAL,
                        recommended_rate TEXT,
                        loan_amount_decision TEXT,
                        input_snapshot TEXT,
                        processed_feature_snapshot TEXT,
                        predicted_at TEXT NOT NULL
                    )"""
                )
            if not self._table_columns(connection, "shap_explanations"):
                connection.execute(
                    """CREATE TABLE shap_explanations (
                        explanation_id TEXT PRIMARY KEY,
                        prediction_id TEXT NOT NULL UNIQUE REFERENCES ml_predictions(prediction_id),
                        base_value REAL,
                        output_space TEXT,
                        feature_contributions TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )"""
                )
            if not self._table_columns(connection, "manual_reviews"):
                connection.execute(
                    """CREATE TABLE manual_reviews (
                        review_id TEXT PRIMARY KEY,
                        application_id TEXT NOT NULL REFERENCES loan_applications(application_id),
                        prediction_id TEXT REFERENCES ml_predictions(prediction_id),
                        reviewer_username TEXT NOT NULL,
                        reviewer_decision TEXT NOT NULL,
                        is_override INTEGER NOT NULL DEFAULT 0,
                        override_reason TEXT,
                        review_notes TEXT,
                        reviewed_at TEXT NOT NULL
                    )"""
                )
            if not self._table_columns(connection, "audit_logs"):
                connection.execute(
                    """CREATE TABLE audit_logs (
                        audit_id TEXT PRIMARY KEY,
                        application_id TEXT NOT NULL REFERENCES loan_applications(application_id),
                        actor TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        old_value TEXT,
                        new_value TEXT,
                        created_at TEXT NOT NULL
                    )"""
                )

            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_datasets_owner ON datasets(owner)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_applicants_dataset ON applicants(dataset_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_loan_applications_owner ON loan_applications(owner)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_loan_applications_reviewer ON loan_applications(assigned_reviewer)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_ml_predictions_application ON ml_predictions(application_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_manual_reviews_application ON manual_reviews(application_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_logs_application ON audit_logs(application_id)"
            )

            for migration_name in (
                "baseline_schema",
                "add_role_support",
                "add_loan_review_schema",
            ):
                if not connection.execute(
                    self._sql(
                        "SELECT 1 FROM schema_migrations WHERE migration_name = ?"
                    ),
                    (migration_name,),
                ).fetchone():
                    self._record_migration(connection, migration_name)

    def create_user(self, username, password_hash, role="analyst"):
        try:
            with self._connection() as connection:
                connection.execute(
                    self._sql("INSERT INTO users (username, password_hash, created_at, role) VALUES (?, ?, ?, ?)"),
                    (username, password_hash, datetime.now(timezone.utc).isoformat(), role),
                )
            return True
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                return False
            raise

    def get_user(self, username):
        with self._connection() as connection:
            row = connection.execute(
                self._sql("SELECT username, password_hash, role FROM users WHERE username=?"),
                (username,),
            ).fetchone()
        if not row:
            return None
        return {"username": row[0], "password_hash": row[1], "role": row[2] if len(row) > 2 else "analyst"}

    def set_user_role(self, username, role):
        with self._connection() as connection:
            cursor = connection.execute(
                self._sql("UPDATE users SET role=? WHERE username=?"),
                (role, username),
            )
            return cursor.rowcount > 0

    def user_has_role(self, username, *roles):
        if not username:
            return False
        role = self.get_user(username)
        if not role:
            return False
        return role.get("role") in roles

    def ensure_user(self, username, password_hash, role="analyst"):
        if not self.get_user(username):
            self.create_user(username, password_hash, role=role)

    def save_dataset(self, dataset_id, owner, filename, analysis, applicants):
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=self.retention_hours)
        with self._connection() as connection:
            connection.execute(
                self._sql("INSERT INTO datasets VALUES (?, ?, ?, ?, ?, ?)"),
                (dataset_id, owner, filename, now.isoformat(), expires.isoformat(), json.dumps(analysis)),
            )
            self._executemany(
                connection,
                self._sql("INSERT INTO applicants VALUES (?, ?, ?, ?, ?)"),
                [
                    (
                        item["id"], dataset_id, item["row_number"],
                        json.dumps(item["data"]), json.dumps(item.get("validation", [])),
                    )
                    for item in applicants
                ],
            )

    def append_current(self, applicants):
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            self._executemany(
                connection,
                self._sql("INSERT INTO current_applicants VALUES (?, ?, ?, ?)"),
                [
                    (item["id"], item["row_number"], json.dumps(item["data"]), now)
                    for item in applicants
                ],
            )

    def get_current_applicants(self):
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT applicant_id, row_number, data_json FROM current_applicants ORDER BY row_number, applicant_id"
            ).fetchall()
        return [
            {"id": row[0], "row_number": row[1], "data": json.loads(row[2]), "validation": []}
            for row in rows
        ]

    def get_dataset(self, dataset_id, owner):
        with self._connection() as connection:
            dataset = connection.execute(
                self._sql("SELECT filename, analysis_json, expires_at FROM datasets WHERE dataset_id=? AND owner=?"),
                (dataset_id, owner),
            ).fetchone()
            if not dataset or datetime.fromisoformat(dataset[2]) < datetime.now(timezone.utc):
                return None
            rows = connection.execute(
                self._sql("SELECT applicant_id, row_number, data_json, validation_json FROM applicants WHERE dataset_id=? ORDER BY row_number"),
                (dataset_id,),
            ).fetchall()
        return {
            "filename": dataset[0],
            "analysis": json.loads(dataset[1]),
            "applicants": [
                {
                    "id": row[0], "row_number": row[1], "data": json.loads(row[2]),
                    "validation": json.loads(row[3]),
                }
                for row in rows
            ],
        }

    def get_applicant(self, dataset_id, applicant_id, owner):
        dataset = self.get_dataset(dataset_id, owner)
        if not dataset:
            return None
        return next((item for item in dataset["applicants"] if item["id"] == applicant_id), None)

    def create_application(
        self,
        owner,
        source_type,
        applicant_snapshot,
        dataset_id=None,
        source_applicant_id=None,
        assigned_reviewer=None,
        status="submitted",
    ):
        application_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute(
                self._sql(
                    "INSERT INTO loan_applications (application_id, owner, assigned_reviewer, source_type, dataset_id, source_applicant_id, applicant_snapshot, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                ),
                (
                    application_id,
                    owner,
                    assigned_reviewer,
                    source_type,
                    dataset_id,
                    source_applicant_id,
                    json.dumps(applicant_snapshot),
                    status,
                    now,
                    now,
                ),
            )
        return application_id

    def get_application(self, application_id, owner=None):
        with self._connection() as connection:
            if owner is None:
                row = connection.execute(
                    self._sql(
                        "SELECT application_id, owner, assigned_reviewer, source_type, dataset_id, source_applicant_id, applicant_snapshot, status, created_at, updated_at FROM loan_applications WHERE application_id=?"
                    ),
                    (application_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    self._sql(
                        "SELECT application_id, owner, assigned_reviewer, source_type, dataset_id, source_applicant_id, applicant_snapshot, status, created_at, updated_at FROM loan_applications WHERE application_id=? AND owner=?"
                    ),
                    (application_id, owner),
                ).fetchone()
        if not row:
            return None
        return {
            "application_id": row[0],
            "owner": row[1],
            "assigned_reviewer": row[2],
            "source_type": row[3],
            "dataset_id": row[4],
            "source_applicant_id": row[5],
            "applicant_snapshot": json.loads(row[6]),
            "status": row[7],
            "created_at": row[8],
            "updated_at": row[9],
        }

    def list_applications(self, owner=None):
        with self._connection() as connection:
            if owner is None:
                rows = connection.execute(
                    "SELECT application_id, owner, assigned_reviewer, source_type, dataset_id, source_applicant_id, applicant_snapshot, status, created_at, updated_at FROM loan_applications ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    self._sql(
                        "SELECT application_id, owner, assigned_reviewer, source_type, dataset_id, source_applicant_id, applicant_snapshot, status, created_at, updated_at FROM loan_applications WHERE owner=? ORDER BY created_at DESC"
                    ),
                    (owner,),
                ).fetchall()
        return [
            {
                "application_id": row[0],
                "owner": row[1],
                "assigned_reviewer": row[2],
                "source_type": row[3],
                "dataset_id": row[4],
                "source_applicant_id": row[5],
                "applicant_snapshot": json.loads(row[6]),
                "status": row[7],
                "created_at": row[8],
                "updated_at": row[9],
            }
            for row in rows
        ]

    def update_application_status(self, application_id, status):
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            cursor = connection.execute(
                self._sql(
                    "UPDATE loan_applications SET status=?, updated_at=? WHERE application_id=?"
                ),
                (status, now, application_id),
            )
            return cursor.rowcount > 0

    def ensure_model_version(self, model_id, metadata):
        with self._connection() as connection:
            connection.execute(
                self._sql(
                    "INSERT INTO model_versions (model_id, model_name, model_version, artifact_hash, model_source, feature_schema, preprocessing_version, calibration_method, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (model_id) DO NOTHING"
                ),
                (
                    model_id,
                    metadata.get("model_name", "home-credit-default-risk"),
                    metadata.get("model_version", model_id),
                    metadata.get("artifact_hash"),
                    metadata.get("model_source"),
                    json.dumps(metadata.get("feature_schema", [])),
                    metadata.get("preprocessing_version"),
                    metadata.get("calibration_method"),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def add_audit_log(self, application_id, actor, event_type, old_value=None, new_value=None):
        audit_id = uuid.uuid4().hex
        with self._connection() as connection:
            connection.execute(
                self._sql(
                    "INSERT INTO audit_logs (audit_id, application_id, actor, event_type, old_value, new_value, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
                ),
                (
                    audit_id,
                    application_id,
                    actor,
                    event_type,
                    json.dumps(old_value) if old_value is not None else None,
                    json.dumps(new_value) if new_value is not None else None,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return audit_id

    def create_prediction(self, application_id, model_id, prediction_payload):
        prediction_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute(
                self._sql(
                    "INSERT INTO ml_predictions (prediction_id, application_id, model_id, default_probability, risk_score, trust_score, confidence, risk_tier, model_decision, approve_threshold, decline_threshold, recommended_rate, loan_amount_decision, input_snapshot, processed_feature_snapshot, predicted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                ),
                (
                    prediction_id,
                    application_id,
                    model_id,
                    float(prediction_payload["default_probability"]),
                    float(prediction_payload["risk_score"]),
                    float(prediction_payload["trust_score"]),
                    float(prediction_payload["confidence"]),
                    prediction_payload.get("risk_tier"),
                    prediction_payload.get("decision"),
                    float(prediction_payload.get("approve_threshold", 0.0)),
                    float(prediction_payload.get("decline_threshold", 1.0)),
                    prediction_payload.get("recommended_rate"),
                    prediction_payload.get("loan_amount_decision"),
                    json.dumps(prediction_payload.get("input_snapshot", {})),
                    json.dumps(prediction_payload.get("processed_feature_snapshot", {})),
                    now,
                ),
            )
        return prediction_id

    def persist_prediction_assessment(
        self, application_id, model_id, prediction_payload, explanation_payload
    ):
        """Persist a prediction and its explanation atomically."""
        prediction_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        explanation_id = uuid.uuid4().hex
        with self._connection() as connection:
            connection.execute(
                self._sql(
                    "INSERT INTO ml_predictions (prediction_id, application_id, model_id, default_probability, risk_score, trust_score, confidence, risk_tier, model_decision, approve_threshold, decline_threshold, recommended_rate, loan_amount_decision, input_snapshot, processed_feature_snapshot, predicted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                ),
                (
                    prediction_id,
                    application_id,
                    model_id,
                    float(prediction_payload["default_probability"]),
                    float(prediction_payload["risk_score"]),
                    float(prediction_payload["trust_score"]),
                    float(prediction_payload["confidence"]),
                    prediction_payload.get("risk_tier"),
                    prediction_payload.get("decision"),
                    float(prediction_payload.get("approve_threshold", 0.0)),
                    float(prediction_payload.get("decline_threshold", 1.0)),
                    prediction_payload.get("recommended_rate"),
                    prediction_payload.get("loan_amount_decision"),
                    json.dumps(prediction_payload.get("input_snapshot", {})),
                    json.dumps(prediction_payload.get("processed_feature_snapshot", {})),
                    now,
                ),
            )
            connection.execute(
                self._sql(
                    "INSERT INTO shap_explanations (explanation_id, prediction_id, base_value, output_space, feature_contributions, created_at) VALUES (?, ?, ?, ?, ?, ?)"
                ),
                (
                    explanation_id,
                    prediction_id,
                    explanation_payload.get("base_value"),
                    explanation_payload.get("output_space"),
                    json.dumps(explanation_payload.get("feature_contributions", [])),
                    now,
                ),
            )
            connection.execute(
                self._sql(
                    "UPDATE loan_applications SET status=?, updated_at=? WHERE application_id=?"
                ),
                ("pending_review", now, application_id),
            )
        return prediction_id

    def save_shap_explanation(self, prediction_id, explanation_payload):
        explanation_id = uuid.uuid4().hex
        with self._connection() as connection:
            connection.execute(
                self._sql(
                    "INSERT INTO shap_explanations (explanation_id, prediction_id, base_value, output_space, feature_contributions, created_at) VALUES (?, ?, ?, ?, ?, ?)"
                ),
                (
                    explanation_id,
                    prediction_id,
                    explanation_payload.get("base_value"),
                    explanation_payload.get("output_space"),
                    json.dumps(explanation_payload.get("feature_contributions", [])),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return explanation_id

    def list_predictions(self, application_id):
        with self._connection() as connection:
            rows = connection.execute(
                self._sql(
                    "SELECT prediction_id, application_id, model_id, default_probability, risk_score, trust_score, confidence, risk_tier, model_decision, approve_threshold, decline_threshold, recommended_rate, loan_amount_decision, input_snapshot, processed_feature_snapshot, predicted_at FROM ml_predictions WHERE application_id=? ORDER BY predicted_at DESC"
                ),
                (application_id,),
            ).fetchall()
        return [
            {
                "prediction_id": row[0],
                "application_id": row[1],
                "model_id": row[2],
                "default_probability": row[3],
                "risk_score": row[4],
                "trust_score": row[5],
                "confidence": row[6],
                "risk_tier": row[7],
                "model_decision": row[8],
                "approve_threshold": row[9],
                "decline_threshold": row[10],
                "recommended_rate": row[11],
                "loan_amount_decision": row[12],
                "input_snapshot": json.loads(row[13]) if row[13] else {},
                "processed_feature_snapshot": json.loads(row[14]) if row[14] else {},
                "predicted_at": row[15],
            }
            for row in rows
        ]

    def create_review(
        self,
        application_id,
        prediction_id,
        reviewer_username,
        reviewer_decision,
        review_notes,
        override_reason=None,
        is_override=False,
    ):
        review_id = uuid.uuid4().hex
        reviewed_at = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute(
                self._sql(
                    "INSERT INTO manual_reviews (review_id, application_id, prediction_id, reviewer_username, reviewer_decision, is_override, override_reason, review_notes, reviewed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                ),
                (
                    review_id,
                    application_id,
                    prediction_id,
                    reviewer_username,
                    reviewer_decision,
                    1 if is_override else 0,
                    override_reason,
                    review_notes,
                    reviewed_at,
                ),
            )
        return review_id

    def finalize_review(
        self,
        application_id,
        prediction_id,
        reviewer_username,
        reviewer_decision,
        review_notes,
        override_reason,
        is_override,
    ):
        """Atomically finalize an application only while it is reviewable."""
        review_id = uuid.uuid4().hex
        reviewed_at = datetime.now(timezone.utc).isoformat()
        status = {
            "approved": "approved",
            "rejected": "rejected",
            "request_information": "needs_information",
        }[reviewer_decision]
        with self._connection() as connection:
            cursor = connection.execute(
                self._sql(
                    "UPDATE loan_applications SET status=?, updated_at=? WHERE application_id=? AND status IN ('submitted', 'pending_review', 'needs_information')"
                ),
                (status, reviewed_at, application_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Application is already finalized or does not exist.")
            connection.execute(
                self._sql(
                    "INSERT INTO manual_reviews (review_id, application_id, prediction_id, reviewer_username, reviewer_decision, is_override, override_reason, review_notes, reviewed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                ),
                (
                    review_id,
                    application_id,
                    prediction_id,
                    reviewer_username,
                    reviewer_decision,
                    1 if is_override else 0,
                    override_reason,
                    review_notes,
                    reviewed_at,
                ),
            )
        return review_id

    def list_reviews(self, application_id):
        with self._connection() as connection:
            rows = connection.execute(
                self._sql(
                    "SELECT review_id, application_id, prediction_id, reviewer_username, reviewer_decision, is_override, override_reason, review_notes, reviewed_at FROM manual_reviews WHERE application_id=? ORDER BY reviewed_at DESC"
                ),
                (application_id,),
            ).fetchall()
        return [
            {
                "review_id": row[0],
                "application_id": row[1],
                "prediction_id": row[2],
                "reviewer_username": row[3],
                "reviewer_decision": row[4],
                "is_override": bool(row[5]),
                "override_reason": row[6],
                "review_notes": row[7],
                "reviewed_at": row[8],
            }
            for row in rows
        ]

    def list_audit_logs(self, application_id):
        with self._connection() as connection:
            rows = connection.execute(
                self._sql(
                    "SELECT audit_id, application_id, actor, event_type, old_value, new_value, created_at FROM audit_logs WHERE application_id=? ORDER BY created_at ASC"
                ),
                (application_id,),
            ).fetchall()
        return [
            {
                "audit_id": row[0],
                "application_id": row[1],
                "actor": row[2],
                "event_type": row[3],
                "old_value": json.loads(row[4]) if row[4] is not None else None,
                "new_value": json.loads(row[5]) if row[5] is not None else None,
                "created_at": row[6],
            }
            for row in rows
        ]
