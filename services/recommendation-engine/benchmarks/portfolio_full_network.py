"""Run the repair-aware portfolio production launch benchmark."""

from __future__ import annotations

import argparse

from trax_io_reco.portfolio.benchmark import (
    FullNetworkBenchmarkConfig,
    run_full_network_benchmark,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keys", type=int, default=58_899)
    parser.add_argument("--tenants", type=int, default=2)
    parser.add_argument("--solver-time-limit", type=float, default=300.0)
    parser.add_argument("--batch-window", type=float, default=900.0)
    args = parser.parse_args()
    config = FullNetworkBenchmarkConfig(
        tenant_ids=tuple(
            f"benchmark-airline-{index + 1}"
            for index in range(args.tenants)
        ),
        key_count_per_tenant=args.keys,
        solver_time_limit_seconds=args.solver_time_limit,
        batch_window_seconds=args.batch_window,
    )
    result = run_full_network_benchmark(config)
    print(result.model_dump_json(indent=2))
    return 0 if result.launch_gate_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
