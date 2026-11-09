"""The standard database checks from omarchy-db: the migrations run from empty, have one head, downgrade step by step and match the models.
On a throwaway Postgres (engine = "postgres"); never the real catalogue."""
import pytest

pytest.importorskip("omarchy_db")      # the standard CI test environment has no omarchy-db; the store workflow runs these
from omarchy_db.testing import test_migrations, test_schema_matches_models  # noqa: E402,F401
