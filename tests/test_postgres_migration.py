import os
from pathlib import Path

import pytest

from Backend.repository import DatasetRepository


@pytest.mark.skipif(
    not os.environ.get("CREDIT_TEST_DATABASE_URL"),
    reason="Set CREDIT_TEST_DATABASE_URL to a disposable PostgreSQL database; production URLs are not used.",
)
def test_postgres_migration_is_repeatable_and_preserves_data():
    database_url = os.environ["CREDIT_TEST_DATABASE_URL"]
    if "production" in database_url.lower():
        pytest.fail("Refusing to run migration tests against a production-looking database URL.")

    repository = DatasetRepository(Path("unused.sqlite3"), database_url=database_url)
    assert repository.create_user("migration-test-user", "test-hash")
    repository.create_application(
        "migration-test-user",
        "manual",
        {"GENDER": "M"},
    )

    DatasetRepository(Path("unused.sqlite3"), database_url=database_url)
    assert repository.get_user("migration-test-user")["username"] == "migration-test-user"
    assert len(repository.list_applications(owner="migration-test-user")) == 1
