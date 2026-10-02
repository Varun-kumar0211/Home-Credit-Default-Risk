import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


class DatasetRepository:
    """Small SQLite-backed repository for dataset sessions and applicants."""

    def __init__(self, path: Path, retention_hours: int = 24):
        self.path = path
        self.retention_hours = retention_hours
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS datasets (
                    dataset_id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                    filename TEXT NOT NULL, created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL, analysis_json TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS applicants (
                    applicant_id TEXT NOT NULL, dataset_id TEXT NOT NULL,
                    row_number INTEGER NOT NULL, data_json TEXT NOT NULL,
                    validation_json TEXT NOT NULL,
                    PRIMARY KEY (dataset_id, applicant_id)
                )"""
            )

    def _connect(self):
        return sqlite3.connect(self.path)

    def save_dataset(self, dataset_id, owner, filename, analysis, applicants):
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=self.retention_hours)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO datasets VALUES (?, ?, ?, ?, ?, ?)",
                (dataset_id, owner, filename, now.isoformat(), expires.isoformat(), json.dumps(analysis)),
            )
            connection.executemany(
                "INSERT INTO applicants VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        item["id"], dataset_id, item["row_number"],
                        json.dumps(item["data"]), json.dumps(item.get("validation", [])),
                    )
                    for item in applicants
                ],
            )

    def get_dataset(self, dataset_id, owner):
        with self._connect() as connection:
            dataset = connection.execute(
                "SELECT filename, analysis_json, expires_at FROM datasets WHERE dataset_id=? AND owner=?",
                (dataset_id, owner),
            ).fetchone()
            if not dataset or datetime.fromisoformat(dataset[2]) < datetime.now(timezone.utc):
                return None
            rows = connection.execute(
                "SELECT applicant_id, row_number, data_json, validation_json FROM applicants WHERE dataset_id=? ORDER BY row_number",
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
