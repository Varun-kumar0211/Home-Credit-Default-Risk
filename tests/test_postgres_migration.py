import os
from pathlib import Path
from uuid import uuid4

import pytest

from Backend.repository import DatasetRepository


def test_postgres_placeholder_conversion():
    repository = DatasetRepository.__new__(DatasetRepository)
    repository.database_url = "postgresql://test-only"

    assert repository._sql(
        "SELECT 1 FROM schema_migrations WHERE migration_name = ?"
    ) == "SELECT 1 FROM schema_migrations WHERE migration_name = %s"
    assert repository._sql(
        "INSERT INTO applicants VALUES (?, ?, ?, ?, ?)"
    ) == "INSERT INTO applicants VALUES (%s, %s, %s, %s, %s)"


@pytest.mark.skipif(
    not os.environ.get("CREDIT_TEST_DATABASE_URL"),
    reason="Set CREDIT_TEST_DATABASE_URL to a disposable PostgreSQL database; production URLs are not used.",
)
def test_postgres_migration_is_repeatable_and_preserves_data():
    database_url = os.environ["CREDIT_TEST_DATABASE_URL"]
    if "production" in database_url.lower():
        pytest.fail("Refusing to run migration tests against a production-looking database URL.")

    repository = DatasetRepository(Path("unused.sqlite3"), database_url=database_url)
    username = "migration-test-" + uuid4().hex
    assert repository.create_user(username, "test-hash")
    repository.create_application(
        username,
        "manual",
        {"GENDER": "M"},
    )

    DatasetRepository(Path("unused.sqlite3"), database_url=database_url)
    assert repository.get_user(username)["username"] == username
    assert len(repository.list_applications(owner=username)) == 1
