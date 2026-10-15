import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


class AssignmentError(ValueError):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


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

            # Fresh, bounded-lived connections avoid stale pooled sockets after Neon idle suspension.
            with psycopg.connect(self.database_url, connect_timeout=10) as connection:
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
            query = "SELECT column_name FROM information_schema.columns WHERE table_name = %s AND table_schema = current_schema()"
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
            columns = self._table_columns(connection, "users")
            for name, definition in (("is_active", "INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))"),
                                     ("version", "INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0)")):
                if name not in columns:
                    connection.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")
            connection.execute("""CREATE TABLE IF NOT EXISTS user_audit_logs (
                audit_id TEXT PRIMARY KEY, target_username TEXT NOT NULL REFERENCES users(username),
                actor TEXT NOT NULL, event_type TEXT NOT NULL, old_value TEXT, new_value TEXT,
                reason TEXT NOT NULL, created_at TEXT NOT NULL)""")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_users_role_active ON users(role, is_active, username)")
            for table in ("user_audit_logs",):
                connection.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_time ON {table}(created_at, audit_id)")
                connection.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_actor ON {table}(actor, created_at)")
                connection.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_event ON {table}(event_type, created_at)")
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
            if "assigned_analyst" not in self._table_columns(connection, "loan_applications"):
                connection.execute("ALTER TABLE loan_applications ADD COLUMN assigned_analyst TEXT REFERENCES users(username)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_applications_owner_page ON loan_applications(owner, created_at, application_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_applications_intake ON loan_applications(source_type, assigned_analyst, status, created_at, application_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_applications_reviewer_page ON loan_applications(assigned_reviewer, created_at, application_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_applications_analyst_page ON loan_applications(assigned_analyst, created_at, application_id)")
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
            connection.execute("CREATE INDEX IF NOT EXISTS idx_manual_reviews_reviewer_page ON manual_reviews(reviewer_username, reviewed_at, application_id, review_id)")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_logs_application ON audit_logs(application_id)"
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_time ON audit_logs(created_at, audit_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_actor ON audit_logs(actor, created_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_event ON audit_logs(event_type, created_at)")

            for migration_name in (
                "baseline_schema",
                "add_role_support",
                "add_loan_review_schema",
                "add_user_management",
                "add_applicant_intake",
            ):
                if not connection.execute(
                    self._sql(
                        "SELECT 1 FROM schema_migrations WHERE migration_name = ?"
                    ),
                    (migration_name,),
                ).fetchone():
                    self._record_migration(connection, migration_name)

    def create_user(self, username, password_hash, role="analyst"):
        if role not in {"analyst", "reviewer", "admin", "applicant"}:
            raise ValueError("Unsupported role.")
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
                self._sql("SELECT username, password_hash, role, is_active, version, created_at FROM users WHERE username=?"),
                (username,),
            ).fetchone()
        if not row:
            return None
        return dict(zip(("username", "password_hash", "role", "is_active", "version", "created_at"), row))

    def set_user_role(self, username, role):
        # Trusted local setup helper; HTTP mutations use change_user instead.
        if role not in {"analyst", "reviewer", "admin", "applicant"}:
            raise ValueError("Unsupported role.")
        with self._connection() as connection:
            cursor = connection.execute(
                self._sql("UPDATE users SET role=?, version=version+1 WHERE username=? AND role<>?"),
                (role, username, role),
            )
            return cursor.rowcount > 0

    def update_user_credentials(self, username, password_hash, role=None):
        """Update password hash (and optionally role) for a local dev/test account."""
        updates = ["password_hash=?", "version=version+1"]
        params: list = [password_hash]
        if role is not None:
            if role not in {"analyst", "reviewer", "admin", "applicant"}:
                raise ValueError("Unsupported role.")
            updates.append("role=?")
            params.append(role)
        params.append(username)
        with self._connection() as connection:
            cursor = connection.execute(
                self._sql(f"UPDATE users SET {', '.join(updates)} WHERE username=?"),
                tuple(params),
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

    def _begin_write(self, connection, manage_users=False):
        if not self.is_postgres:
            connection.execute("BEGIN IMMEDIATE")
        elif manage_users:
            # Serialize account management, including last-Admin count checks.
            connection.execute("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE")

    def _locked_accounts(self, connection, usernames, exclusive=False):
        result = {}
        for username in sorted(set(name for name in usernames if name)):
            row = connection.execute(self._sql(
                "SELECT username, role, is_active, version, created_at FROM users WHERE username=?"
                + ((" FOR UPDATE" if exclusive else " FOR SHARE") if self.is_postgres else "")), (username,)).fetchone()
            result[username] = dict(zip(("username", "role", "is_active", "version", "created_at"), row)) if row else None
        return result

    @staticmethod
    def _authorize_write(user, roles, expected_version=None):
        if not user or not user["is_active"] or (expected_version is not None and user["version"] != expected_version):
            raise AssignmentError(401, "User session is no longer available.")
        if user["role"] not in roles:
            raise AssignmentError(403, "Account does not authorize this operation.")

    @staticmethod
    def _eligible_reviewer(user):
        if not user:
            raise AssignmentError(404, "Target reviewer not found.")
        if user["role"] != "reviewer" or not user["is_active"]:
            raise AssignmentError(422, "The target account must be an active Reviewer.")

    def list_users(self, limit=25, offset=0):
        if not 1 <= limit <= 100 or offset < 0:
            raise AssignmentError(422, "Invalid pagination.")
        with self._connection() as connection:
            rows = connection.execute(self._sql(
                "SELECT username, role, is_active, version, created_at FROM users ORDER BY username LIMIT ? OFFSET ?"),
                (limit, offset)).fetchall()
            total = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        return {"users": [dict(zip(("username", "role", "is_active", "version", "created_at"),
                                   (row[0], row[1], bool(row[2]), row[3], row[4]))) for row in rows],
                "total": total, "limit": limit, "offset": offset}

    def change_user(self, actor, actor_version, username, expected_version, reason, *, role=None, is_active=None):
        if (not reason.strip() or type(expected_version) is not int or expected_version < 0
                or (role is None) == (is_active is None)
                or (role is not None and role not in {"analyst", "reviewer", "admin"})
                or (is_active is not None and type(is_active) is not bool)):
            raise AssignmentError(422, "Invalid user change.")
        with self._connection() as connection:
            self._begin_write(connection, manage_users=True)
            accounts = self._locked_accounts(connection, [actor, username], exclusive=True)
            self._authorize_write(accounts[actor], {"admin"}, actor_version)
            target = accounts[username]
            if not target:
                raise AssignmentError(404, "User not found.")
            if target["version"] != expected_version:
                raise AssignmentError(409, "Account changed. Refresh before updating.")
            next_role = role if role is not None else target["role"]
            next_active = is_active if is_active is not None else bool(target["is_active"])
            if role is not None and target["role"] == "applicant":
                raise AssignmentError(422, "Applicant accounts cannot be converted to staff accounts.")
            if next_role == target["role"] and next_active == bool(target["is_active"]):
                raise AssignmentError(422, "Account already has the requested state.")
            if actor == username and (next_role != "admin" or not next_active):
                raise AssignmentError(409, "Admin self-lockout is not permitted.")
            if target["role"] == "admin" and target["is_active"] and (next_role != "admin" or not next_active):
                count = connection.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND is_active=1").fetchone()[0]
                if count <= 1:
                    raise AssignmentError(409, "The last active Admin must be retained.")
            if target["role"] == "reviewer" and (next_role != "reviewer" or not next_active):
                pending = connection.execute(self._sql("""SELECT 1 FROM loan_applications la
                    WHERE assigned_reviewer=? AND status IN ('submitted','pending_review','needs_information')
                    AND NOT EXISTS (SELECT 1 FROM manual_reviews mr WHERE mr.application_id=la.application_id
                                    AND mr.reviewer_decision IN ('approved','rejected')) LIMIT 1"""), (username,)).fetchone()
                if pending:
                    raise AssignmentError(409, "Reassign pending applications before changing this Reviewer.")
            if target["role"] == "analyst" and (next_role != "analyst" or not next_active):
                pending = connection.execute(self._sql("SELECT 1 FROM loan_applications WHERE assigned_analyst=? AND status IN ('submitted','pending_review','needs_information') LIMIT 1"), (username,)).fetchone()
                if pending:
                    raise AssignmentError(409, "Complete claimed applications before changing this Analyst.")
            old = {"role": target["role"], "is_active": bool(target["is_active"]), "version": target["version"]}
            new = {"role": next_role, "is_active": next_active, "version": target["version"] + 1}
            cursor = connection.execute(self._sql(
                "UPDATE users SET role=?, is_active=?, version=version+1 WHERE username=? AND version=?"),
                (next_role, int(next_active), username, expected_version))
            if cursor.rowcount != 1:
                raise AssignmentError(409, "Account changed. Refresh before updating.")
            event = "user_role_changed" if role is not None else ("user_activated" if next_active else "user_deactivated")
            connection.execute(self._sql("""INSERT INTO user_audit_logs
                (audit_id,target_username,actor,event_type,old_value,new_value,reason,created_at)
                VALUES (?,?,?,?,?,?,?,?)"""), (uuid.uuid4().hex, username, actor, event,
                json.dumps(old), json.dumps(new), reason.strip(), datetime.now(timezone.utc).isoformat()))
        return {"username": username, **new, "created_at": target["created_at"]}

    @staticmethod
    def sanitize_audit(event):
        allowed = {
            "application_created": {"source_type", "dataset_id", "source_applicant_id", "reviewer_username"},
            "prediction_generated": {"prediction_id"},
            "analyst_claimed": {"assigned_analyst", "assigned_reviewer"},
            "assignment_changed": {"assigned_reviewer", "reason"},
            "user_role_changed": {"role", "is_active", "version"},
            "user_activated": {"role", "is_active", "version"},
            "user_deactivated": {"role", "is_active", "version"},
        }
        result = {key: event.get(key) for key in
                  ("audit_id", "application_id", "actor", "event_type", "created_at")}
        for key in ("scope", "target_username", "reason"):
            if key in event:
                result[key] = event[key]
        for key in ("old_value", "new_value"):
            value = event.get(key)
            if event["event_type"] == "manual_review":
                result[key] = value if value in ("submitted", "pending_review", "needs_information", "approved", "rejected") else None
            elif isinstance(value, dict):
                result[key] = {name: item for name, item in value.items()
                               if name in allowed.get(event["event_type"], set())
                               and (item is None or type(item) in (str, int, bool))}
            else:
                result[key] = None
        return result

    def list_admin_audit(self, limit=25, offset=0, event_type=None, actor=None,
                         application_id=None, date_from=None, date_to=None):
        if not 1 <= limit <= 100 or offset < 0:
            raise AssignmentError(422, "Invalid pagination.")
        union = """SELECT audit_id,application_id,NULL AS target_username,actor,event_type,
            old_value,new_value,NULL AS reason,created_at,'application' AS scope FROM audit_logs
            UNION ALL SELECT audit_id,NULL,target_username,actor,event_type,
            old_value,new_value,reason,created_at,'user' FROM user_audit_logs"""
        conditions, params = [], []
        for field, value, operator in (("event_type", event_type, "="), ("actor", actor, "="),
                                      ("application_id", application_id, "="),
                                      ("created_at", date_from, ">="), ("created_at", date_to, "<=")):
            if value is not None:
                conditions.append(f"{field}{operator}?")
                params.append(value)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._connection() as connection:
            total = connection.execute(self._sql(f"SELECT COUNT(*) FROM ({union}) events{where}"), tuple(params)).fetchone()[0]
            rows = connection.execute(self._sql(f"SELECT * FROM ({union}) events{where} ORDER BY created_at DESC,scope,audit_id DESC LIMIT ? OFFSET ?"),
                                      tuple(params + [limit, offset])).fetchall()
        events = []
        for row in rows:
            event = dict(zip(("audit_id", "application_id", "target_username", "actor", "event_type",
                              "old_value", "new_value", "reason", "created_at", "scope"), row))
            for key in ("old_value", "new_value"):
                try:
                    event[key] = json.loads(event[key]) if event[key] is not None else None
                except (ValueError, TypeError):
                    event[key] = None
            events.append(self.sanitize_audit(event))
        return {"events": events, "total": total, "limit": limit, "offset": offset}

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
        actor_version=None,
    ):
        application_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            self._begin_write(connection)
            accounts = self._locked_accounts(connection, [owner, assigned_reviewer])
            if actor_version is not None:
                self._authorize_write(accounts[owner], {"applicant"} if source_type == "applicant" else {"analyst", "admin"}, actor_version)
            if assigned_reviewer:
                self._eligible_reviewer(accounts[assigned_reviewer])
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
            if source_type == "applicant":
                connection.execute(self._sql("INSERT INTO audit_logs (audit_id, application_id, actor, event_type, old_value, new_value, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)"),
                    (uuid.uuid4().hex, application_id, owner, "application_created", None, json.dumps({"source_type": "applicant"}), now))
        return application_id

    def get_application(self, application_id, owner=None):
        with self._connection() as connection:
            if owner is None:
                row = connection.execute(
                    self._sql(
                        "SELECT application_id, owner, assigned_reviewer, source_type, dataset_id, source_applicant_id, applicant_snapshot, status, created_at, updated_at, assigned_analyst FROM loan_applications WHERE application_id=?"
                    ),
                    (application_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    self._sql(
                        "SELECT application_id, owner, assigned_reviewer, source_type, dataset_id, source_applicant_id, applicant_snapshot, status, created_at, updated_at, assigned_analyst FROM loan_applications WHERE application_id=? AND owner=?"
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
            "assigned_analyst": row[10],
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

    @staticmethod
    def public_application(application, detail=False):
        status = application["status"]
        progress = {"pending_review": "Awaiting Review", "approved": "Approved", "rejected": "Rejected",
                    "needs_information": "Additional Information Required"}.get(status,
                    "Being Assessed" if application.get("assigned_analyst") else "Submitted / Awaiting Intake")
        result = {key: application[key] for key in ("application_id", "status", "created_at", "updated_at")}
        result.update(progress=progress, loan_amount=application["applicant_snapshot"].get("CREDIT_AMOUNT"))
        if detail:
            fields = {"GENDER", "QUALIFICATION", "FAMILY_STATUS", "OCCUPATION", "CONTRACT_TYPE", "TOTAL_INCOME",
                      "CREDIT_AMOUNT", "ANNUAL_LOAN_PAYMENT", "GOODS_PRICE", "AGE", "YEARS_OF_EXPERIENCE", "CREDIT_SCORE", "CREDIT_HISTORY"}
            result["applicant_snapshot"] = {key: value for key, value in application["applicant_snapshot"].items() if key in fields}
        return result

    def application_page(self, *, role, username, limit=25, offset=0, intake=False, pending=False):
        if not 1 <= limit <= 100 or offset < 0:
            raise AssignmentError(422, "Invalid pagination.")
        params = []
        if intake:
            clause = "source_type='applicant' AND assigned_analyst IS NULL AND status='submitted'"
        elif role == "applicant":
            clause = "owner=? AND source_type='applicant'"
            params = [username]
        elif role == "analyst":
            clause = "((owner=? AND source_type<>'applicant') OR assigned_analyst=?)"
            params = [username, username]
        elif role == "reviewer":
            clause = "assigned_reviewer=? AND (source_type<>'applicant' OR status<>'submitted')"
            params = [username]
        elif role == "admin":
            clause = "1=1"
        else:
            raise AssignmentError(403, "Staff access is required.")
        if pending:
            clause += " AND status IN ('submitted','pending_review','needs_information')"
        with self._connection() as connection:
            rows = connection.execute(self._sql(f"SELECT application_id, owner, assigned_reviewer, source_type, dataset_id, source_applicant_id, applicant_snapshot, status, created_at, updated_at, assigned_analyst, (SELECT COUNT(*) FROM ml_predictions p WHERE p.application_id=loan_applications.application_id) FROM loan_applications WHERE {clause} ORDER BY created_at DESC, application_id DESC LIMIT ? OFFSET ?"),
                                      tuple(params + [limit, offset])).fetchall()
            total = connection.execute(self._sql(f"SELECT COUNT(*) FROM loan_applications WHERE {clause}"), tuple(params)).fetchone()[0]
        keys = ("application_id", "owner", "assigned_reviewer", "source_type", "dataset_id", "source_applicant_id", "applicant_snapshot", "status", "created_at", "updated_at", "assigned_analyst", "prediction_count")
        applications = []
        for row in rows:
            item = dict(zip(keys, row))
            item["applicant_snapshot"] = json.loads(item["applicant_snapshot"])
            if role == "applicant":
                item = self.public_application(item)
            elif intake:
                item = {key: item[key] for key in ("application_id", "owner", "created_at", "updated_at", "status")}
            applications.append(item)
        return {"applications": applications, "total": total, "limit": limit, "offset": offset}

    def claim_application(self, application_id, actor, actor_version, reviewer_username, expected_updated_at):
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            self._begin_write(connection)
            accounts = self._locked_accounts(connection, [actor, reviewer_username])
            self._authorize_write(accounts[actor], {"analyst"}, actor_version)
            self._eligible_reviewer(accounts[reviewer_username])
            row = connection.execute(self._sql("SELECT source_type, status, assigned_analyst, updated_at FROM loan_applications WHERE application_id=?"
                + (" FOR UPDATE" if self.is_postgres else "")), (application_id,)).fetchone()
            if not row:
                raise AssignmentError(404, "Application not found.")
            if row[0] != "applicant" or row[1] != "submitted" or row[2] is not None or row[3] != expected_updated_at:
                raise AssignmentError(409, "Application changed or was already claimed. Refresh intake.")
            connection.execute(self._sql("UPDATE loan_applications SET assigned_analyst=?, assigned_reviewer=?, updated_at=? WHERE application_id=?"),
                               (actor, reviewer_username, now, application_id))
            connection.execute(self._sql("INSERT INTO audit_logs (audit_id,application_id,actor,event_type,old_value,new_value,created_at) VALUES (?,?,?,?,?,?,?)"),
                (uuid.uuid4().hex, application_id, actor, "analyst_claimed", json.dumps({"assigned_analyst": None, "assigned_reviewer": None}),
                 json.dumps({"assigned_analyst": actor, "assigned_reviewer": reviewer_username}), now))
        return {"application_id": application_id, "assigned_analyst": actor, "assigned_reviewer": reviewer_username,
                "status": "submitted", "updated_at": now}

    def review_history_page(self, username, limit=25, offset=0):
        with self._connection() as connection:
            rows = connection.execute(self._sql("""SELECT mr.application_id, mr.reviewer_decision, mr.reviewed_at,
                mr.is_override, mr.override_reason, mr.review_notes FROM manual_reviews mr
                WHERE mr.reviewer_username=? AND NOT EXISTS
                (SELECT 1 FROM manual_reviews newer WHERE newer.application_id=mr.application_id
                 AND newer.reviewer_username=mr.reviewer_username
                 AND (newer.reviewed_at>mr.reviewed_at OR (newer.reviewed_at=mr.reviewed_at AND newer.review_id>mr.review_id)))
                ORDER BY mr.reviewed_at DESC, mr.application_id DESC LIMIT ? OFFSET ?"""), (username, limit, offset)).fetchall()
            total = connection.execute(self._sql("SELECT COUNT(DISTINCT application_id) FROM manual_reviews WHERE reviewer_username=?"), (username,)).fetchone()[0]
        keys = ("application_id", "final_decision", "review_date", "is_override", "override_reason", "notes")
        return {"history": [dict(zip(keys, row)) for row in rows], "total": total, "limit": limit, "offset": offset}

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
        self, application_id, model_id, prediction_payload, explanation_payload,
        actor=None, actor_version=None, expected_updated_at=None,
    ):
        """Persist a prediction and its explanation atomically."""
        prediction_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        explanation_id = uuid.uuid4().hex
        with self._connection() as connection:
            self._begin_write(connection)
            if actor is not None:
                accounts = self._locked_accounts(connection, [actor])
                self._authorize_write(accounts[actor], {"analyst", "admin"}, actor_version)
            application = connection.execute(self._sql("SELECT owner, source_type, assigned_analyst, status, updated_at FROM loan_applications WHERE application_id=?"
                + (" FOR UPDATE" if self.is_postgres else "")), (application_id,)).fetchone()
            if not application:
                raise AssignmentError(404, "Application not found.")
            if actor is not None and accounts[actor]["role"] != "admin" and not (
                    (application[1] != "applicant" and application[0] == actor) or application[2] == actor):
                raise AssignmentError(409, "Application assignment changed. Refresh before assessing.")
            if application[3] not in {"submitted", "pending_review", "needs_information"} or (
                    expected_updated_at is not None and application[4] != expected_updated_at):
                raise AssignmentError(409, "Application changed or is finalized. Refresh before assessing.")
            if application[1] == "applicant" and not application[2]:
                raise AssignmentError(409, "An Analyst must claim this application before assessment.")
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
            if actor is not None:
                connection.execute(self._sql("INSERT INTO audit_logs (audit_id,application_id,actor,event_type,old_value,new_value,created_at) VALUES (?,?,?,?,?,?,?)"),
                    (uuid.uuid4().hex, application_id, actor, "prediction_generated", None, json.dumps({"prediction_id": prediction_id}), now))
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

    def get_shap_explanation(self, prediction_id):
        with self._connection() as connection:
            row = connection.execute(
                self._sql(
                    "SELECT explanation_id, prediction_id, base_value, output_space, feature_contributions, created_at FROM shap_explanations WHERE prediction_id=?"
                ),
                (prediction_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "explanation_id": row[0],
            "prediction_id": row[1],
            "base_value": row[2],
            "output_space": row[3],
            "feature_contributions": json.loads(row[4]) if row[4] else [],
            "created_at": row[5],
        }

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
        expected_updated_at=None,
        actor_version=None,
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
            self._begin_write(connection)
            accounts = self._locked_accounts(connection, [reviewer_username])
            self._authorize_write(accounts[reviewer_username], {"reviewer", "admin"}, actor_version)
            application = connection.execute(self._sql(
                "SELECT status, assigned_reviewer, owner FROM loan_applications WHERE application_id=?"
                + (" FOR UPDATE" if self.is_postgres else "")), (application_id,)).fetchone()
            if application is None:
                raise AssignmentError(404, "Application not found.")
            if accounts[reviewer_username]["role"] != "admin" and application[1] != reviewer_username:
                raise AssignmentError(409, "Application changed. Refresh before reviewing.")
            prediction = connection.execute(self._sql(
                "SELECT model_decision FROM ml_predictions WHERE prediction_id=? AND application_id=?"),
                (prediction_id, application_id)).fetchone()
            if not prediction:
                raise AssignmentError(400, "A prediction for this application is required.")
            expected = {"Auto Approve": "approved", "Auto Decline": "rejected",
                        "Manual Review Required": "request_information"}.get(prediction[0])
            is_override = expected != reviewer_decision
            if is_override and not (override_reason or "").strip():
                raise AssignmentError(422, "An override reason is required.")
            version_clause = " AND updated_at=?" if expected_updated_at is not None else ""
            parameters = (status, reviewed_at, application_id)
            if expected_updated_at is not None:
                parameters += (expected_updated_at,)
            cursor = connection.execute(
                self._sql(
                    "UPDATE loan_applications SET status=?, updated_at=? WHERE application_id=? AND status IN ('submitted', 'pending_review', 'needs_information')"
                    + version_clause
                ),
                parameters,
            )
            if cursor.rowcount != 1:
                raise ValueError("Application changed or is already finalized. Refresh before reviewing.")
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
            connection.execute(self._sql("""INSERT INTO audit_logs
                (audit_id,application_id,actor,event_type,old_value,new_value,created_at)
                VALUES (?,?,?,?,?,?,?)"""), (uuid.uuid4().hex, application_id, reviewer_username,
                "manual_review", json.dumps(application[0]), json.dumps(status), reviewed_at))
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

    def list_admin_assignments(self, limit=25, offset=0):
        with self._connection() as connection:
            rows = connection.execute(self._sql("""
                SELECT la.application_id, la.owner, la.assigned_reviewer, la.status,
                       la.created_at, la.updated_at,
                       EXISTS (SELECT 1 FROM manual_reviews mr
                               WHERE mr.application_id=la.application_id
                               AND mr.reviewer_decision IN ('approved', 'rejected')),
                       la.assigned_analyst, la.source_type
                FROM loan_applications la
                ORDER BY la.created_at DESC, la.application_id DESC LIMIT ? OFFSET ?
            """), (limit, offset)).fetchall()
            total = connection.execute("SELECT COUNT(*) FROM loan_applications").fetchone()[0]
        return {
            "applications": [dict(zip(
                ("application_id", "owner", "assigned_reviewer", "status", "created_at", "updated_at", "has_final_review", "assigned_analyst", "source_type"),
                row
            ), can_reassign=row[3] in {"submitted", "pending_review", "needs_information"} and not row[6] and (row[8] != "applicant" or row[7] is not None)) for row in rows],
            "total": total, "limit": limit, "offset": offset,
        }

    def reassign_application(self, application_id, actor, reviewer_username, reason, expected_updated_at, actor_version=None):
        """Lock, validate, update and audit together; the caller supplies its observed version."""
        if not reason.strip():
            raise AssignmentError(422, "A reassignment reason is required.")
        with self._connection() as connection:
            # SQLite serializes writers before reading. PostgreSQL locks this application row.
            self._begin_write(connection)
            accounts = self._locked_accounts(connection, [actor, reviewer_username])
            self._authorize_write(accounts[actor], {"admin"}, actor_version)
            self._eligible_reviewer(accounts[reviewer_username])
            lock = " FOR UPDATE" if self.is_postgres else ""
            row = connection.execute(self._sql(
                "SELECT assigned_reviewer, status, updated_at, source_type, assigned_analyst FROM loan_applications WHERE application_id=?" + lock
            ), (application_id,)).fetchone()
            if row is None:
                raise AssignmentError(404, "Application not found.")
            old_reviewer, status, updated_at, source_type, assigned_analyst = row
            if source_type == "applicant" and assigned_analyst is None:
                raise AssignmentError(409, "An Analyst must claim this application before Reviewer reassignment.")
            final_review = connection.execute(self._sql(
                "SELECT 1 FROM manual_reviews WHERE application_id=? AND reviewer_decision IN ('approved','rejected') LIMIT 1"
            ), (application_id,)).fetchone()
            if status not in {"submitted", "pending_review", "needs_information"} or final_review:
                raise AssignmentError(409, "Applications with a final decision cannot be reassigned.")
            if updated_at != expected_updated_at:
                raise AssignmentError(409, "Application changed. Refresh before reassigning.")
            if old_reviewer == reviewer_username:
                raise AssignmentError(422, "Application is already assigned to this reviewer.")
            now = datetime.now(timezone.utc).isoformat()
            cursor = connection.execute(self._sql(
                "UPDATE loan_applications SET assigned_reviewer=?, updated_at=? WHERE application_id=? AND updated_at=?"
            ), (reviewer_username, now, application_id, expected_updated_at))
            if cursor.rowcount != 1:
                raise AssignmentError(409, "Application changed. Refresh before reassigning.")
            connection.execute(self._sql(
                "INSERT INTO audit_logs (audit_id, application_id, actor, event_type, old_value, new_value, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
            ), (uuid.uuid4().hex, application_id, actor, "assignment_changed",
                json.dumps({"assigned_reviewer": old_reviewer}),
                json.dumps({"assigned_reviewer": reviewer_username, "reason": reason.strip()}), now))
        return {"application_id": application_id, "assigned_reviewer": reviewer_username,
                "previous_reviewer": old_reviewer, "status": status, "updated_at": now}

    def get_analyst_portfolio_summary(self):
        """Aggregate outcomes only; no unrelated applicant identities or finances."""
        with self._connection() as connection:
            counts = dict(connection.execute("SELECT status, COUNT(*) FROM loan_applications GROUP BY status").fetchall())
        total = sum(counts.values())
        approved, rejected = counts.get("approved", 0), counts.get("rejected", 0)
        return {"total": total, "approved": approved, "rejected": rejected,
                "pending": total - approved - rejected,
                "approval_rate": round(100 * approved / total, 2) if total else 0,
                "rejection_rate": round(100 * rejected / total, 2) if total else 0}

    def get_admin_portfolio_overview(self):
        """Return aggregated portfolio KPIs and reviewer workload.

        Uses a single DB connection with SQL aggregations — no per-applicant
        loops, no ML inference, no SHAP. Compatible with SQLite and PostgreSQL.
        """
        with self._connection() as connection:
            # --- KPI counts by status ---
            status_rows = connection.execute(
                "SELECT status, COUNT(*) FROM loan_applications GROUP BY status"
            ).fetchall()
            status_counts = {row[0]: row[1] for row in status_rows}

            total = sum(status_counts.values())
            approved = status_counts.get("approved", 0)
            rejected = status_counts.get("rejected", 0)
            pending_review = status_counts.get("pending_review", 0)
            submitted = status_counts.get("submitted", 0)
            needs_information = status_counts.get("needs_information", 0)

            # "Pending Reviews" = awaiting a final human decision
            pending = pending_review + needs_information
            # "Awaiting Assessment" = submitted but no ML prediction yet
            awaiting_assessment = submitted

            # --- Reviewer workload (single aggregation query) ---
            workload_rows = connection.execute(
                self._sql("""
                    SELECT
                        u.username,
                        COUNT(la.application_id) AS total_assigned,
                        SUM(CASE WHEN la.status NOT IN ('approved','rejected') THEN 1 ELSE 0 END) AS pending_count,
                        SUM(CASE WHEN la.status IN ('approved','rejected') THEN 1 ELSE 0 END) AS finalized_count
                    FROM users u
                    LEFT JOIN loan_applications la
                        ON la.assigned_reviewer = u.username
                    WHERE u.role = 'reviewer'
                    GROUP BY u.username
                    ORDER BY u.username ASC
                """)
            ).fetchall()

            reviewer_workload = [
                {
                    "username": row[0],
                    "total_assigned": row[1] or 0,
                    "pending": row[2] or 0,
                    "finalized": row[3] or 0,
                }
                for row in workload_rows
            ]

            # Unassigned applications
            unassigned_row = connection.execute(
                "SELECT COUNT(*) FROM loan_applications WHERE assigned_reviewer IS NULL OR assigned_reviewer = ''"
            ).fetchone()
            unassigned_count = unassigned_row[0] if unassigned_row else 0

            # --- Recent applications (bounded to 10) ---
            recent_rows = connection.execute(
                "SELECT application_id, owner, assigned_reviewer, status, created_at FROM loan_applications ORDER BY created_at DESC LIMIT 10"
            ).fetchall()
            recent_applications = [
                {
                    "application_id": row[0],
                    "owner": row[1],
                    "assigned_reviewer": row[2],
                    "status": row[3],
                    "created_at": row[4],
                }
                for row in recent_rows
            ]

        return {
            "totals": {
                "total": total,
                "approved": approved,
                "rejected": rejected,
                "pending_review": pending_review,
                "submitted": submitted,
                "needs_information": needs_information,
                "pending": pending,
                "awaiting_assessment": awaiting_assessment,
            },
            "status_distribution": status_counts,
            "reviewer_workload": reviewer_workload,
            "unassigned_count": unassigned_count,
            "recent_applications": recent_applications,
        }
