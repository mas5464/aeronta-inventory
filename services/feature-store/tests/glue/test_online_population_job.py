"""The deployed population driver uses the shared writer and fails partial runs."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from trax_io_feature_store.glue.online_population_job import (
    _parse_args,
    run_population,
)


def test_population_job_maps_deployment_arguments_to_shared_runtime() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    expected = SimpleNamespace(
        keys=(("PN-A", "LOC-1"),),
        population=SimpleNamespace(
            written=1,
            skipped_incomplete=0,
            failed_oversize=0,
            failed_writes=0,
            committed_generation="generation-1",
        ),
    )

    def populate(tenant_id: str, **kwargs):
        calls.append((tenant_id, kwargs))
        return expected

    result = run_population(
        {
            "tenant_id": "acme",
            "online_table_name": "acme-online",
            "catalog_name": "glue",
            "catalog_type": "glue",
            "catalog_database": "trax_io_lake_acme",
            "table_prefix": "raw_",
            "warehouse": "s3://lake/",
            "demand_window": "36",
        },
        populate=populate,
    )

    assert result is expected
    assert calls == [
        (
            "acme",
            {
                "environ": {
                    "TRAX_IO_FEATURE_ONLINE_TABLE": "acme-online",
                    "TRAX_IO_FEATURE_CATALOG_NAME": "glue",
                    "TRAX_IO_FEATURE_CATALOG_TYPE": "glue",
                    "TRAX_IO_FEATURE_WAREHOUSE": "s3://lake/",
                    "TRAX_IO_FEATURE_NAMESPACE": "trax_io_lake_acme",
                    "TRAX_IO_FEATURE_TABLE_PREFIX": "raw_",
                },
                "demand_window": 36,
            },
        )
    ]


def test_population_job_fails_when_any_dynamo_write_failed() -> None:
    result = SimpleNamespace(
        keys=(("PN-A", "LOC-1"),),
        population=SimpleNamespace(
            written=0,
            skipped_incomplete=0,
            failed_oversize=1,
            failed_writes=0,
            committed_generation=None,
        ),
    )

    with pytest.raises(RuntimeError, match="1 DynamoDB item"):
        run_population(
            {"tenant_id": "acme", "online_table_name": "online"},
            populate=lambda *args, **kwargs: result,
        )


@pytest.mark.parametrize("demand_window", ["0", "-1"])
def test_population_job_rejects_nonpositive_window(demand_window: str) -> None:
    with pytest.raises(ValueError, match="demand_window must be positive"):
        run_population(
            {
                "tenant_id": "acme",
                "online_table_name": "online",
                "demand_window": demand_window,
            },
            populate=lambda *args, **kwargs: None,
        )


def test_population_job_requires_tenant_and_table() -> None:
    with pytest.raises(ValueError, match="online_table_name"):
        _parse_args(["--tenant_id", "acme"])
