"""The standard database checks from strata_python.db: the migrations run from empty, have one head, downgrade step by step and match the models.
On a throwaway Postgres (engine = "postgres"); never the real catalogue."""

from strata_python.db.testing import test_migrations, test_schema_matches_models  # noqa: F401
