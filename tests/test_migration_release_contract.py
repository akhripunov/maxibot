from pathlib import Path
import importlib.util
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "migration_release_contract", ROOT / "scripts/migration_release_contract.py"
)
CONTRACT_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTRACT_MODULE)


def _contract(source_sha="a" * 40):
    return {
        "version": 1,
        "task": "ME-57",
        "owner_authorization": {
            "status": "OWNER_AUTHORIZED",
            "reference": "ME-57: OWNER Metrica Reconciliation V1 + Migration 0099",
            "risk_category": "schema_migration",
        },
        "source_application_sha": source_sha,
        "source_revision": "0098_source",
        "target_revision": "0099_target",
        "migration_paths": ["alembic/versions/0099_target.py"],
        "approved_paths": [
            "alembic/versions/0099_target.py",
            "app/db/models.py",
            "docs/release/migration-risk-contract.json",
            "scripts/postgres_migration_rehearsal.py",
        ],
        "frozen_paths": ["app/db/models.py"],
        "rehearsal_script": "scripts/postgres_migration_rehearsal.py",
        "preserved_tables": ["offline_conversion_queue"],
        "schema": {
            "source": {
                "required_columns": [{"table": "records", "name": "id", "data_type": "bigint"}],
                "absent_columns": [{"table": "records", "name": "new_value"}],
                "absent_indexes": [],
                "absent_constraints": [],
            },
            "target": {
                "columns": [{"table": "records", "name": "new_value", "data_type": "text", "nullable": True}],
                "indexes": [],
                "constraints": [],
            },
        },
        "resume_allowed": True,
        "recovery": {"database": "forward_only", "application_rollback_sha": source_sha},
    }


def _graph(revision="0099_target", parent="0098_source", path="alembic/versions/0099_target.py"):
    return [
        {"revision": "0098_source", "parents": [], "path": "alembic/versions/0098_source.py"},
        {"revision": revision, "parents": [parent], "path": path},
    ]


def test_app_only_candidate_keeps_the_existing_release_path():
    result = CONTRACT_MODULE.evaluate_contract(
        _contract(), ["app/main.py"], "a" * 40, "0098_source", _graph()
    )
    assert result == {"migration_required": False, "migration_resume": False}


def test_authorized_migration_is_derived_from_the_linear_alembic_graph():
    result = CONTRACT_MODULE.evaluate_contract(
        _contract(), _contract()["approved_paths"], "a" * 40, "0098_source", _graph()
    )
    assert result["migration_required"] is True
    assert result["migration_resume"] is False
    assert result["source_revision"] == "0098_source"
    assert result["target_revision"] == "0099_target"
    assert result["migration_paths"] == ["alembic/versions/0099_target.py"]


def test_migration_contract_rejects_empty_source_or_target_schema_postconditions():
    for replace_schema in (
        lambda schema: schema.update(source={"required_columns": [], "absent_columns": [], "absent_indexes": [], "absent_constraints": []}),
        lambda schema: schema.update(target={"columns": [], "indexes": [], "constraints": []}),
    ):
        contract = _contract()
        replace_schema(contract["schema"])
        try:
            CONTRACT_MODULE.evaluate_contract(
                contract, contract["approved_paths"], "a" * 40, "0098_source", _graph()
            )
        except ValueError as error:
            assert "schema" in str(error)
        else:
            raise AssertionError("migration with an empty schema contract was accepted")


def test_synthetic_0100_uses_the_same_graph_and_contract_mechanism():
    contract = _contract()
    contract.update(
        task="ME-SYNTHETIC-0100",
        source_revision="0099_target",
        target_revision="0100_synthetic",
        migration_paths=["alembic/versions/0100_synthetic.py"],
        approved_paths=[
            "alembic/versions/0100_synthetic.py",
            "docs/release/migration-risk-contract.json",
            "scripts/postgres_migration_rehearsal.py",
            "app/db/models.py",
        ],
        frozen_paths=["app/db/models.py"],
        preserved_tables=["synthetic_records"],
        schema={
            "source": {
                "required_columns": [{"table": "records", "name": "id", "data_type": "bigint"}],
                "absent_columns": [{"table": "records", "name": "created_by_0100"}],
                "absent_indexes": ["ix_records_created_by_0100"],
                "absent_constraints": ["fk_records_created_by_0100"],
            },
            "target": {
                "columns": [{"table": "records", "name": "created_by_0100", "data_type": "text", "nullable": True}],
                "indexes": [{"table": "records", "name": "ix_records_created_by_0100", "definition": "CREATE INDEX ix_records_created_by_0100 ON public.records USING btree (created_by_0100)", "method": "btree", "unique": False, "valid": True, "ready": True}],
                "constraints": [{"table": "records", "name": "fk_records_created_by_0100", "type": "f", "definition": "FOREIGN KEY (created_by_0100) REFERENCES owners(id)", "validated": True}],
            },
        },
    )
    graph = [
        {"revision": "0099_target", "parents": [], "path": "alembic/versions/0099_target.py"},
        {"revision": "0100_synthetic", "parents": ["0099_target"], "path": "alembic/versions/0100_synthetic.py"},
    ]
    result = CONTRACT_MODULE.evaluate_contract(
        contract, contract["approved_paths"], "a" * 40, "0099_target", graph
    )
    assert result["migration_required"] is True
    assert result["target_revision"] == "0100_synthetic"
    assert result["migration_paths"] == ["alembic/versions/0100_synthetic.py"]


def test_synthetic_0100_schema_contract_accepts_clean_source_and_exact_target_only():
    contract = _contract()
    contract["schema"] = {
        "source": {
            "required_columns": [{"table": "records", "name": "id", "data_type": "bigint"}],
            "absent_columns": [{"table": "records", "name": "created_by_0100"}],
            "absent_indexes": ["ix_records_created_by_0100"],
            "absent_constraints": ["fk_records_created_by_0100"],
        },
        "target": {
            "columns": [{"table": "records", "name": "created_by_0100", "data_type": "text", "nullable": True}],
            "indexes": [{"table": "records", "name": "ix_records_created_by_0100", "definition": "CREATE INDEX ix_records_created_by_0100 ON public.records USING btree (created_by_0100)", "method": "btree", "unique": False, "valid": True, "ready": True}],
            "constraints": [{"table": "records", "name": "fk_records_created_by_0100", "type": "f", "definition": "FOREIGN KEY (created_by_0100) REFERENCES owners(id)", "validated": True}],
        },
    }
    source = {"columns": [{"table": "records", "name": "id", "data_type": "bigint"}], "indexes": [], "constraints": []}
    target = {"columns": source["columns"] + [{"table": "records", "name": "created_by_0100", "data_type": "text", "nullable": True}], "indexes": [{"table": "records", "name": "ix_records_created_by_0100", "definition": "CREATE INDEX ix_records_created_by_0100 ON public.records USING btree (created_by_0100)", "method": "btree", "unique": False, "valid": True, "ready": True}], "constraints": [{"table": "records", "name": "fk_records_created_by_0100", "type": "f", "definition": "FOREIGN KEY (created_by_0100) REFERENCES owners(id)", "validated": True}]}
    CONTRACT_MODULE.validate_schema_snapshot(contract["schema"], source, "source")
    CONTRACT_MODULE.validate_schema_snapshot(contract["schema"], target, "target")

    for bad_snapshot, mode in (
        ({"columns": source["columns"] + [{"table": "records", "name": "created_by_0100", "data_type": "text", "nullable": True}], "indexes": []}, "source"),
        ({"columns": target["columns"], "indexes": []}, "target"),
        ({"columns": target["columns"], "indexes": [{**target["indexes"][0], "valid": False}]}, "target"),
        ({"columns": target["columns"], "indexes": target["indexes"], "constraints": [{**target["constraints"][0], "validated": False}]}, "target"),
    ):
        try:
            CONTRACT_MODULE.validate_schema_snapshot(contract["schema"], bad_snapshot, mode)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid {mode} schema snapshot was accepted")


def test_schema_verifier_cli_prints_one_reusable_postgresql_catalog_query():
    result = subprocess.run(
        ["python3", str(ROOT / "scripts/verify_migration_schema_contract.py"), "--print-query"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    assert "information_schema.columns" in result.stdout
    assert "pg_get_indexdef" in result.stdout
    assert "pg_get_constraintdef" in result.stdout


def test_unapproved_scope_or_authorization_is_rejected():
    contract = _contract()
    for change in (
        lambda c: c["owner_authorization"].update(status="NOT_AUTHORIZED"),
        lambda c: c.update(source_application_sha="b" * 40),
    ):
        candidate = _contract()
        change(candidate)
        try:
            CONTRACT_MODULE.evaluate_contract(
                candidate, candidate["approved_paths"], "a" * 40, "0098_source", _graph()
            )
        except ValueError:
            pass
        else:
            raise AssertionError("invalid migration authorization was accepted")

    try:
        CONTRACT_MODULE.evaluate_contract(
            contract, [*contract["approved_paths"], "docker-compose.vps.yml"],
            "a" * 40, "0098_source", _graph()
        )
    except ValueError:
        pass
    else:
        raise AssertionError("unrelated production infrastructure path was accepted")


def test_wrong_or_partial_database_head_is_rejected_and_exact_target_is_resume():
    contract = _contract()
    try:
        CONTRACT_MODULE.evaluate_contract(
            contract, contract["approved_paths"], "a" * 40, "0098_partial", _graph()
        )
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown partial database state was accepted")

    result = CONTRACT_MODULE.evaluate_contract(
        contract, contract["approved_paths"], "a" * 40, "0099_target", _graph()
    )
    assert result["migration_resume"] is True


def test_migration_chain_must_be_linear_and_match_contract_paths():
    contract = _contract()
    graph = _graph()
    graph.append({"revision": "0099_fork", "parents": ["0098_source"], "path": "alembic/versions/0099_fork.py"})
    try:
        CONTRACT_MODULE.evaluate_contract(
            contract, contract["approved_paths"], "a" * 40, "0098_source", graph
        )
    except ValueError:
        pass
    else:
        raise AssertionError("ambiguous Alembic heads were accepted")


def test_target_must_be_the_only_current_alembic_head():
    contract = _contract()
    graph = _graph()
    graph.append({"revision": "0100_unapproved", "parents": ["0099_target"], "path": "alembic/versions/0100_unapproved.py"})
    try:
        CONTRACT_MODULE.evaluate_contract(
            contract, contract["approved_paths"], "a" * 40, "0098_source", graph
        )
    except ValueError:
        pass
    else:
        raise AssertionError("contract target that is not the candidate Alembic head was accepted")


def test_contract_requires_safe_recovery_and_rejects_compose_or_alembic_config_only_changes():
    contract = _contract()
    contract["recovery"]["database"] = "downgrade"
    try:
        CONTRACT_MODULE.evaluate_contract(
            contract, contract["approved_paths"], "a" * 40, "0098_source", _graph()
        )
    except ValueError:
        pass
    else:
        raise AssertionError("destructive or unspecified database recovery was accepted")

    for path in ("docker-compose.vps.yml", "alembic.ini", "alembic/env.py"):
        try:
            CONTRACT_MODULE.evaluate_contract(
                _contract(), [path], "a" * 40, "0098_source", _graph()
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"unscoped migration infrastructure change was accepted: {path}")


def test_owner_authorization_requires_owner_publication_on_protected_main():
    validate = CONTRACT_MODULE.validate_owner_main_publication
    validate("refs/heads/main", "akhripunov", "akhripunov")
    for ref, actor, owner in (
        ("refs/pull/62/merge", "akhripunov", "akhripunov"),
        ("refs/heads/main", "contributor", "akhripunov"),
    ):
        try:
            validate(ref, actor, owner)
        except ValueError:
            pass
        else:
            raise AssertionError("candidate-declared authorization was accepted without owner main publication")


def test_changed_historical_alembic_revision_is_rejected_even_with_approved_paths():
    contract = _contract()
    changed = [*contract["approved_paths"], "alembic/versions/0098_source.py"]
    contract["approved_paths"] = changed
    try:
        CONTRACT_MODULE.evaluate_contract(
            contract, changed, "a" * 40, "0098_source", _graph()
        )
    except ValueError as error:
        assert "changed Alembic revision files" in str(error)
    else:
        raise AssertionError("edited historical Alembic revision was accepted")


def test_source_schema_allows_only_exact_recoverable_index_states():
    schema = _contract()["schema"]
    index = {
        "table": "records",
        "name": "ix_records_new_value",
        "definition": "CREATE INDEX ix_records_new_value ON public.records USING btree (new_value)",
        "method": "btree",
        "unique": False,
        "valid": True,
        "ready": True,
    }
    schema["source"]["recoverable_indexes"] = [{"table": "records", "name": index["name"]}]
    schema["target"]["indexes"] = [index]
    source = {
        "columns": [{"table": "records", "name": "id", "data_type": "bigint"}],
        "indexes": [],
        "constraints": [],
    }

    # A clean source has no index; a completed valid index or an interrupted
    # concurrent build can be resumed by the migration's exact-index logic.
    CONTRACT_MODULE.validate_schema_snapshot(schema, source, "source")
    for valid, ready in ((True, True), (False, True), (False, False)):
        observed = {**index, "valid": valid, "ready": ready}
        CONTRACT_MODULE.validate_schema_snapshot(
            schema, {**source, "indexes": [observed]}, "source"
        )

    invalid_states = (
        {**index, "valid": True, "ready": False},
        {**index, "definition": index["definition"] + " WHERE new_value IS NOT NULL"},
        {**index, "method": "hash"},
        {**index, "unique": True},
        {**index, "table": "other_table"},
    )
    for observed in invalid_states:
        try:
            CONTRACT_MODULE.validate_schema_snapshot(
                schema, {**source, "indexes": [observed]}, "source"
            )
        except ValueError:
            pass
        else:
            raise AssertionError("incompatible source catalog index was accepted")


def test_target_index_contract_cannot_accept_invalid_or_unready_indexes():
    for field in ("valid", "ready"):
        contract = _contract()
        contract["schema"]["target"]["indexes"] = [{
            "table": "records",
            "name": "ix_records_new_value",
            "definition": "CREATE INDEX ix_records_new_value ON public.records USING btree (new_value)",
            "method": "btree",
            "unique": False,
            "valid": True,
            "ready": True,
        }]
        contract["schema"]["target"]["indexes"][0][field] = False
        try:
            CONTRACT_MODULE.evaluate_contract(
                contract, contract["approved_paths"], "a" * 40, "0098_source", _graph()
            )
        except ValueError as error:
            assert "valid and ready" in str(error)
        else:
            raise AssertionError(f"target index with {field}=false was accepted")
