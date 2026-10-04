import json
import sqlite3
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

    def _initialize(self):
        statements = [
            """CREATE TABLE IF NOT EXISTS datasets (
                dataset_id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                filename TEXT NOT NULL, created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL, analysis_json TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS applicants (
                applicant_id TEXT NOT NULL, dataset_id TEXT NOT NULL,
                row_number INTEGER NOT NULL, data_json TEXT NOT NULL,
                validation_json TEXT NOT NULL,
                PRIMARY KEY (dataset_id, applicant_id)
            )""",
            """CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS current_applicants (
                applicant_id TEXT PRIMARY KEY, row_number INTEGER NOT NULL,
                data_json TEXT NOT NULL, created_at TEXT NOT NULL
            )""",
        ]
        with self._connection() as connection:
            for statement in statements:
                connection.execute(statement)

    def create_user(self, username, password_hash):
        try:
            with self._connection() as connection:
                connection.execute(
                    self._sql("INSERT INTO users VALUES (?, ?, ?)"),
                    (username, password_hash, datetime.now(timezone.utc).isoformat()),
                )
            return True
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                return False
            raise

    def get_user(self, username):
        with self._connection() as connection:
            row = connection.execute(
                self._sql("SELECT username, password_hash FROM users WHERE username=?"),
                (username,),
            ).fetchone()
        return {"username": row[0], "password_hash": row[1]} if row else None

    def ensure_user(self, username, password_hash):
        if not self.get_user(username):
            self.create_user(username, password_hash)

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
                "INSERT INTO applicants VALUES (?, ?, ?, ?, ?)",
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
                "INSERT INTO current_applicants VALUES (?, ?, ?, ?)",
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
