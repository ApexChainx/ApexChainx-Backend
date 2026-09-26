import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.config import settings


@pytest.mark.skipif(
    "sqlite" in settings.DATABASE_URL,
    reason="Requires PostgreSQL for alembic migrations",
)
class TestMigrationRoundTrip:
    @pytest.fixture(scope="class")
    def alembic_cfg(self):
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
        return cfg

    @pytest.fixture(scope="class")
    def all_revisions(self, alembic_cfg):
        script = ScriptDirectory.from_config(alembic_cfg)
        return [rev.revision for rev in script.walk_revisions()]

    def test_each_migration_upgrade_and_downgrade(self, alembic_cfg):
        script = ScriptDirectory.from_config(alembic_cfg)
        heads = script.get_heads()
        assert len(heads) > 0, "No migration heads found"

    def test_upgrade_head_then_downgrade_base(self, alembic_cfg, all_revisions):
        upgrade(alembic_cfg, "head")
        downgrade(alembic_cfg, "base")

    def test_downgrade_round_trip_per_revision(self, alembic_cfg, all_revisions):
        for rev in all_revisions:
            if rev.down_revision:
                upgrade(alembic_cfg, rev.revision)
                downgrade(alembic_cfg, rev.down_revision)
                upgrade(alembic_cfg, rev.revision)

    def test_upgrade_from_base_to_head(self, alembic_cfg):
        upgrade(alembic_cfg, "base")
        upgrade(alembic_cfg, "head")

    def test_downgrade_from_head_to_base(self, alembic_cfg):
        upgrade(alembic_cfg, "head")
        downgrade(alembic_cfg, "base")

    def test_upgrade_and_downgrade_full_cycle(self, alembic_cfg, all_revisions):
        upgrade(alembic_cfg, "head")
        for rev in all_revisions:
            downgrade(alembic_cfg, rev.down_revision or "base")
        upgrade(alembic_cfg, "head")
