"""The standard database checks from omarchy-db: the migrations run from empty, have one head, downgrade step by step and match the models.
On a throwaway Postgres (engine = "postgres"); never the real catalogue."""

from omarchy_db.testing import test_migrations, test_schema_matches_models  # noqa: F401
