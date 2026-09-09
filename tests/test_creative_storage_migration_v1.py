from pathlib import Path


def test_creative_storage_keeps_canonical_sixteen_table_contract():
    source = Path("alembic/versions/0080_creative_storage_intelligence_v1.py").read_text()
    assert 'revision = "0080_creative_storage_intelligence_v1"' in source
    assert 'down_revision = "0079_ads_objective_target_cpa_v1"' in source
    assert source.count('op.add_column("ads_creatives"') == 12
    assert 'op.create_index("ix_ads_creatives_source_type"' in source
    assert "op.create_table" not in source

