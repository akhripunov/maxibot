from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts/frozen_path_guard.sh"
MANIFEST = ROOT / "docs/frozen_paths.yaml"


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _case(tmp_path: Path, changes: dict[str, str], base_files: dict[str, str] | None = None) -> int:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docs").mkdir()
    (repo / "scripts").mkdir()
    shutil.copy2(GUARD, repo / "scripts/frozen_path_guard.sh")
    shutil.copy2(MANIFEST, repo / "docs/frozen_paths.yaml")
    for name, content in (base_files or {}).items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    _git(repo, "config", "user.email", "guard-test@example.invalid")
    _git(repo, "config", "user.name", "guard-test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    for name, content in changes.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "change")
    head = _git(repo, "rev-parse", "HEAD")
    result = subprocess.run(
        [str(repo / "scripts/frozen_path_guard.sh"), "--repo", str(repo), "--base", base, "--head", head],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return result.returncode


BRIDGE = '''"""Widen Alembic's version column before long ADS revisions are recorded."""
from alembic import op
import sqlalchemy as sa
revision = "0075a_alembic_version_width"
down_revision = "0075_ads_canonical_v2"
branch_labels = None
depends_on = None
def upgrade() -> None:
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
def downgrade() -> None:
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=64),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
'''


def test_bridge_only_passes(tmp_path):
    assert _case(tmp_path, {"alembic/versions/0075a_alembic_version_width.py": BRIDGE}) == 0


def test_0076_down_revision_only_passes(tmp_path):
    old = 'down_revision = "0075_ads_canonical_v2"\n'
    new = 'down_revision = "0075a_alembic_version_width"\n'
    assert _case(tmp_path, {"alembic/versions/0076_ads_directologist_decision_engine_v1.py": new}, {
        "alembic/versions/0076_ads_directologist_decision_engine_v1.py": old,
    }) == 0


def test_bridge_plus_exact_0076_change_passes(tmp_path):
    old = 'down_revision = "0075_ads_canonical_v2"\n'
    new = 'down_revision = "0075a_alembic_version_width"\n'
    assert _case(tmp_path, {
        "alembic/versions/0075a_alembic_version_width.py": BRIDGE,
        "alembic/versions/0076_ads_directologist_decision_engine_v1.py": new,
    }, {
        "alembic/versions/0076_ads_directologist_decision_engine_v1.py": old,
    }) == 0


def test_bridge_plus_unrelated_migration_blocks(tmp_path):
    assert _case(tmp_path, {
        "alembic/versions/0075a_alembic_version_width.py": BRIDGE,
        "alembic/versions/0099_unrelated.py": 'op.create_table("unrelated")\n',
    }) != 0


def test_bridge_plus_frozen_domains_blocks(tmp_path):
    for name, content in {
        "app/main.py": "# BOT runtime change\n",
        "app/bot/runtime.py": "# CHANNEL runtime change\n",
        "app/traffic/system_conversion_processor.py": "# Metrika change\n",
        "app/traffic/system_conversion.py": "# paid_dedupe change\n",
        "app/db/models.py": "# cross_object change\n",
    }.items():
        case = tmp_path / name.replace("/", "_")
        case.mkdir()
        assert _case(case, {
            "alembic/versions/0075a_alembic_version_width.py": BRIDGE,
            name: content,
        }) != 0


def test_0076_extra_change_blocks(tmp_path):
    old = 'down_revision = "0075_ads_canonical_v2"\n'
    new = 'down_revision = "0075a_alembic_version_width"\n# unrelated change\n'
    assert _case(tmp_path, {
        "alembic/versions/0076_ads_directologist_decision_engine_v1.py": new,
    }, {
        "alembic/versions/0076_ads_directologist_decision_engine_v1.py": old,
    }) != 0


OBJECTIVE_0079 = '''"""Objective V1."""
from alembic import op
import sqlalchemy as sa
revision = "0079_ads_objective_target_cpa_v1"
down_revision = "0078_ads_bot_ai_support_v1"
branch_labels = None
depends_on = None
def upgrade() -> None:
    op.add_column("ads_promotion_campaign_links", sa.Column("objective_type", sa.String(length=32), nullable=True))
    op.add_column("ads_promotion_campaign_links", sa.Column("target_cpa", sa.Numeric(20, 6), nullable=True))
    op.add_column("ads_promotion_campaign_links", sa.Column("currency", sa.String(length=8), nullable=True))
    op.add_column("ads_promotion_campaign_links", sa.Column("objective_status", sa.String(length=16), nullable=True))
    op.add_column("ads_promotion_campaign_links", sa.Column("objective_updated_at", sa.DateTime(timezone=True), nullable=True))
def downgrade() -> None:
    op.drop_column("ads_promotion_campaign_links", "objective_updated_at")
    op.drop_column("ads_promotion_campaign_links", "objective_status")
    op.drop_column("ads_promotion_campaign_links", "currency")
    op.drop_column("ads_promotion_campaign_links", "target_cpa")
    op.drop_column("ads_promotion_campaign_links", "objective_type")
'''


def test_objective_0079_ads_only_migration_passes(tmp_path):
    assert _case(tmp_path, {"alembic/versions/0079_ads_objective_target_cpa_v1.py": OBJECTIVE_0079}) == 0


def test_objective_0079_extra_table_blocks(tmp_path):
    assert _case(tmp_path, {
        "alembic/versions/0079_ads_objective_target_cpa_v1.py": OBJECTIVE_0079.replace(
            'op.add_column("ads_promotion_campaign_links", sa.Column("objective_type", sa.String(length=32), nullable=True))',
            'op.add_column("ads_promotion_campaign_links", sa.Column("objective_type", sa.String(length=32), nullable=True))\n    op.add_column("traffic_visits", sa.Column("bad", sa.String(length=8), nullable=True))',
        ),
    }) != 0
