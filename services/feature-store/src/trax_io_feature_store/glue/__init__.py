"""PySpark Glue transforms for Trax IO feature groups.

Each module in this package is a Phase 2 Glue ETL job that consumes raw
nightly-extract artifacts described by an `ExtractManifest` and produces
rows for one of the 10 v1 feature groups.

The Phase 2 template slice ships `demand_history_job` only; the remaining
nine feature groups follow the same pattern and will be added as separate
modules here.
"""

from __future__ import annotations
