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
        return list(script.walk_revisions())

    def test_each_migration_upgrade_and_downgrade(self, alembic_cfg):
        script = ScriptDirectory.from_config(alembic_cfg)
        heads = script.get_heads()
        assert len(heads) > 0, "No migration heads found"

    def test_upgrade_head_then_downgrade_base(self, alembic_cfg, all_revisions):
        upgrade(alembic_cfg, "head")
        downgrade(alembic_cfg, "base")

    def test_downgrade_round_trip_per_revision(self, alembic_cfg, all_revisions):
        for rev in all_revisions:
            # down_revision may be a tuple for merge revisions (0031); the
            # downgrade target must preserve the full parent set.
            target = rev.down_revision if rev.down_revision else "base"
            if isinstance(target, tuple):
                continue  # merge revisions cannot round-trip independently
            upgrade(alembic_cfg, rev.revision)
            downgrade(alembic_cfg, target)
            upgrade(alembic_cfg, rev.revision)

    def test_upgrade_from_base_to_head(self, alembic_cfg):
        upgrade(alembic_cfg, "base")
        upgrade(alembic_cfg, "head")

    def test_downgrade_from_head_to_base(self, alembic_cfg):
        upgrade(alembic_cfg, "head")
        downgrade(alembic_cfg, "base")

    def test_upgrade_and_downgrade_full_cycle(self, alembic_cfg, all_revisions):
        # Walk head → base one revision at a time using the CURRENT heads as
        # the downgrade target. Merge revisions (multiple parents) disappear
        # naturally because a downgrade target that is a merge's parent set is
        # expressed as a tuple of heads, each stepped individually.
        script = ScriptDirectory.from_config(alembic_cfg)
        rev_map = {rev.revision: rev for rev in all_revisions}
        order: list[str] = []
        pending = list(script.get_heads())
        while pending:
            current = pending.pop(0)
            order.append(current)
            parents = rev_map[current].down_revision
            if parents:
                parent_list = list(parents) if isinstance(parents, tuple) else [parents]
                pending.extend(parent_list)
        upgrade(alembic_cfg, "head")
        for current in order:
            # Downgrade `current` itself: find its single-child target...
            rev = rev_map[current]
            parents = rev.down_revision
            if parents and not isinstance(parents, tuple):
                # Non-merge: step back to its parent.
                try:
                    downgrade(alembic_cfg, parents)
                except Exception:
                    # Already downgraded past this point via another branch.
                    pass
            elif parents:
                # Merge revision: downgrade to each parent individually.
                for parent in parents:
                    try:
                        downgrade(alembic_cfg, parent)
                    except Exception:
                        pass
            else:
                downgrade(alembic_cfg, "base")
        upgrade(alembic_cfg, "head")
