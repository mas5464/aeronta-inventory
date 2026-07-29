from __future__ import annotations

import json
import sys

import pytest
from trax_io_reco.replay.package import build_trusted_replay_package_file

from tests.replay_builders import (
    matched_replay_source_package,
    replay_request,
)
from trax_io_spine.pg import replay_import
from trax_io_spine.pg.replay import PgReplayRunStore
from trax_io_spine.pg.replay_import import import_replay_universe

TENANT_UUID = "66666666-6666-6666-6666-666666660001"
TENANT_SLUG = "replay-import-t1"
OTHER_TENANT_UUID = "66666666-6666-6666-6666-666666660002"


@pytest.fixture(autouse=True)
def tenants(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            """
            insert into tenants (id, slug, name)
            values (%s::uuid, %s, 'Replay Import One')
            on conflict (id) do nothing
            """,
            (TENANT_UUID, TENANT_SLUG),
        )
        conn.execute(
            """
            insert into tenants (id, slug, name)
            values (%s::uuid, 'replay-import-t2', 'Replay Import Two')
            on conflict (id) do nothing
            """,
            (OTHER_TENANT_UUID,),
        )
        conn.execute(
            "delete from replay_runs where tenant_id in (%s::uuid, %s::uuid)",
            (TENANT_UUID, OTHER_TENANT_UUID),
        )
        conn.execute(
            "delete from replay_universes where tenant_id in (%s::uuid, %s::uuid)",
            (TENANT_UUID, OTHER_TENANT_UUID),
        )
    yield
    with admin_pool.connection() as conn:
        conn.execute(
            "delete from replay_runs where tenant_id in (%s::uuid, %s::uuid)",
            (TENANT_UUID, OTHER_TENANT_UUID),
        )
        conn.execute(
            "delete from replay_universes where tenant_id in (%s::uuid, %s::uuid)",
            (TENANT_UUID, OTHER_TENANT_UUID),
        )


def _write_request(tmp_path, request, name: str):
    path = tmp_path / name
    path.write_text(
        json.dumps(request.model_dump(mode="json")),
        encoding="utf-8",
    )
    return path


def test_service_import_is_validated_idempotent_and_discoverable(
    seed_pool,
    pg_pool,
    tmp_path,
) -> None:
    first_request = replay_request(
        TENANT_SLUG,
        universe_id="operator-package-a",
    )
    second_request = replay_request(
        TENANT_SLUG,
        universe_id="operator-package-b",
    )
    first_path = _write_request(tmp_path, first_request, "first.json")
    second_path = _write_request(tmp_path, second_request, "second.json")

    first = import_replay_universe(
        seed_pool,
        tenant_uuid=TENANT_UUID,
        universe_ref="approved-a",
        input_path=first_path,
    )
    repeated = import_replay_universe(
        seed_pool,
        tenant_uuid=TENANT_UUID,
        universe_ref="approved-a",
        input_path=first_path,
    )
    import_replay_universe(
        seed_pool,
        tenant_uuid=TENANT_UUID,
        universe_ref="approved-b",
        input_path=second_path,
    )

    assert repeated == first
    store = PgReplayRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planner-user",
    )
    page, total = store.list_universes(limit=1)
    all_items, all_total = store.list_universes(limit=100)
    assert total == all_total == 2
    assert len(page) == 1
    assert {item.universe_ref for item in all_items} == {
        "approved-a",
        "approved-b",
    }
    assert all(item.expected_decision_count == 1 for item in all_items)
    assert all(item.observation_count + item.exclusion_count == 1 for item in all_items)


def test_source_builder_output_is_import_ready(
    seed_pool,
    pg_pool,
    tmp_path,
) -> None:
    source = matched_replay_source_package(
        TENANT_SLUG,
        universe_id="repository-source-package",
    )
    source_path = tmp_path / "historical-source.json"
    request_path = tmp_path / "replay-evaluation-request.json"
    source_path.write_text(source.model_dump_json(), encoding="utf-8")
    request = build_trusted_replay_package_file(
        input_path=source_path,
        output_path=request_path,
    )

    imported = import_replay_universe(
        seed_pool,
        tenant_uuid=TENANT_UUID,
        universe_ref="repository-source-package",
        input_path=request_path,
    )

    assert imported.expected_decision_count == 1
    assert imported.observation_count == 1
    assert imported.exclusion_count == 0
    assert request.observations[0].current_lineage.planning_run_id == (
        "11111111-1111-1111-1111-111111111111"
    )
    store = PgReplayRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planner-user",
    )
    listed, total = store.list_universes()
    assert total == 1
    assert listed[0].universe_ref == "repository-source-package"


def test_import_fails_closed_for_app_role_tenant_mismatch_and_invalid_package(
    seed_pool,
    pg_pool,
    tmp_path,
) -> None:
    request = replay_request(TENANT_SLUG, universe_id="fail-closed-package")
    valid_path = _write_request(tmp_path, request, "valid.json")
    invalid_payload = request.model_dump(mode="json")
    invalid_payload["browser_supplied_observations"] = []
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text(json.dumps(invalid_payload), encoding="utf-8")

    with pytest.raises(PermissionError, match="service seed role"):
        import_replay_universe(
            pg_pool,
            tenant_uuid=TENANT_UUID,
            universe_ref="app-role-denied",
            input_path=valid_path,
        )
    with pytest.raises(ValueError, match="tenant"):
        import_replay_universe(
            seed_pool,
            tenant_uuid=OTHER_TENANT_UUID,
            universe_ref="cross-tenant-denied",
            input_path=valid_path,
        )
    with pytest.raises(ValueError):
        import_replay_universe(
            seed_pool,
            tenant_uuid=TENANT_UUID,
            universe_ref="invalid-package-denied",
            input_path=invalid_path,
        )


def test_universe_listing_is_bounded_and_tenant_scoped(
    seed_pool,
    pg_pool,
    tmp_path,
) -> None:
    request = replay_request(TENANT_SLUG, universe_id="tenant-listing")
    import_replay_universe(
        seed_pool,
        tenant_uuid=TENANT_UUID,
        universe_ref="tenant-listing-ref",
        input_path=_write_request(tmp_path, request, "listing.json"),
    )
    owner = PgReplayRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planner-user",
    )
    other = PgReplayRunStore(
        pg_pool,
        tenant_slug="replay-import-t2",
        tenant_uuid=OTHER_TENANT_UUID,
        principal="planner-user",
    )

    assert owner.list_universes()[1] == 1
    assert other.list_universes() == ((), 0)
    with pytest.raises(ValueError, match="limit"):
        owner.list_universes(limit=101)
    with pytest.raises(ValueError, match="offset"):
        owner.list_universes(offset=-1)


def test_cli_redacts_package_and_database_failures(
    monkeypatch,
    capsys,
) -> None:
    sentinel = "SECRET-HISTORICAL-FACT-MUST-NOT-LEAK"

    class _Pool:
        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "trax_io_spine.pg.db.make_pool",
        lambda _database_url: _Pool(),
    )

    def fail_import(*_args, **_kwargs):
        raise ValueError(f"invalid payload input_value={sentinel}")

    monkeypatch.setattr(replay_import, "import_replay_universe", fail_import)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "trax-io-replay-import",
            "--database-url",
            "postgresql://redacted",
            "--tenant-uuid",
            TENANT_UUID,
            "--universe-ref",
            "redaction-test",
            "--input",
            "/controlled/package.json",
        ],
    )

    with pytest.raises(SystemExit) as exited:
        replay_import.main()

    captured = capsys.readouterr()
    assert exited.value.code == 1
    assert sentinel not in captured.out
    assert sentinel not in captured.err
    assert "trusted replay import failed" in captured.err
