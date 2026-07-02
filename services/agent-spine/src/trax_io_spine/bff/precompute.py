"""Offline precompute batch: run the full recommendation engine ONCE and persist the
result as JSON, so the BFF can seed a `PlannerStore` at boot without recomputing.

At 62K keys with the statistical projector, `RecommendationService.run` takes tens of
minutes — unacceptable to run inline at container boot (see `PlannerStore.from_extract`).
This CLI runs that computation offline and writes a complete snapshot dir:
`recs.json` (a JSON array of `Recommendation.model_dump(mode="json")`),
`feature_store.json` (the built, pooled feature store — see
`trax_io_feature_store.snapshot`), `keys.json` (the planning-key universe),
`manifest.json` (copied for the feeds view), and `meta.json` (run metadata).
`PlannerStore.from_snapshot_dir` (in `store.py`) boots from that dir with no
extract parsing at all; the older `from_snapshot` (recs-only) path still works.

JSON, not pickle: the offline host and the container may run different Python versions
(e.g. 3.14 vs 3.12), and pickle is not guaranteed compatible across them. Pydantic's
`model_dump(mode="json")` / `model_validate` round-trip is version-stable.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from trax_io_feature_store import TenantContext
from trax_io_feature_store.snapshot import SNAPSHOT_FORMAT, dump_store
from trax_io_forecasting.projector import StatisticalProjector
from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.service import RecommendationService


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="trax-io-precompute",
        description=(
            "Run the recommendation engine once over an extract dir and persist a "
            "complete boot snapshot (recs + feature store + keys + manifest — see "
            "PlannerStore.from_snapshot_dir)."
        ),
    )
    p.add_argument("--extract-dir", required=True, help="Path to a nightly-extract output dir")
    p.add_argument("--tenant", required=True, help="Tenant id to seed")
    p.add_argument(
        "--now", required=True,
        help="ISO 'now' for the run (e.g. 2026-04-01T00:00:00+00:00)",
    )
    p.add_argument(
        "--out", required=True,
        help="Output directory for the snapshot (recs.json, meta.json, "
        "feature_store.json, keys.json, manifest.json)",
    )
    p.add_argument(
        "--pool-by-part", dest="pool_by_part", action="store_true",
        help="Network-pool on-hand/demand across physical locations (real eMRO extracts)",
    )
    p.add_argument(
        "--no-pool-by-part", dest="pool_by_part", action="store_false",
        help="Per-location stock/demand, no pooling (default; matches the committed sample)",
    )
    p.set_defaults(pool_by_part=False)
    p.add_argument(
        "--projector", choices=["historical", "statistical"], default="historical",
        help="Demand projector: 'historical' (deterministic, default) or 'statistical' "
        "(#5 StatisticalProjector — Croston/SBA/TSB for the intermittent regime)",
    )
    return p.parse_args(argv)


def run(args: argparse.Namespace) -> dict:
    """Execute the precompute batch and write recs.json + meta.json. Returns the meta dict."""
    started = time.monotonic()
    now = datetime.fromisoformat(args.now).astimezone(UTC)

    fs, inv, tenant_id, keys = build_stores_from_extract(
        args.extract_dir, tenant_id=args.tenant, pool_by_part=args.pool_by_part
    )
    tenant = TenantContext(tenant_id=tenant_id)
    projector = StatisticalProjector() if args.projector == "statistical" else None
    batch = RecommendationService(
        feature_store=fs, inventory_state=inv, projector=projector
    ).run(tenant=tenant, keys=keys, now=now)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    recs_payload = [rec.model_dump(mode="json") for rec in batch.recommendations]
    (out_dir / "recs.json").write_text(json.dumps(recs_payload))

    # Fast-boot snapshot: persist the BUILT (pooled) feature store + the keys universe
    # + the manifest, so PlannerStore.from_snapshot_dir boots with no extract parsing,
    # no pooling, and no engine run (spec: 2026-07-02-fast-boot-feature-store-snapshot).
    stats = dump_store(fs, out_dir / "feature_store.json")
    (out_dir / "keys.json").write_text(json.dumps([list(k) for k in keys]))
    manifest_src = Path(args.extract_dir) / "manifest.json"
    if manifest_src.exists():
        shutil.copyfile(manifest_src, out_dir / "manifest.json")

    elapsed = time.monotonic() - started
    meta = {
        "tenant": tenant_id,
        "now": now.isoformat(),
        "pool_by_part": args.pool_by_part,
        "projector": args.projector,
        "count": len(recs_payload),
        "keys": len(keys),
        "snapshot_format": SNAPSHOT_FORMAT,
        "elapsed_seconds": round(elapsed, 3),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta))

    print(
        f"precomputed {meta['count']} recommendations + feature-store snapshot "
        f"({stats['unique_values']} unique values / {stats['entries']} entries) "
        f"in {elapsed:.2f}s -> {out_dir}"
    )
    return meta


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    run(args)


if __name__ == "__main__":
    main()
    sys.exit(0)
