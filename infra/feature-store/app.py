"""CDK app entrypoint for the Trax IO Feature Store.

Synth only. Do NOT `cdk deploy` from this scaffold — Phase 8 of the plan
introduces the per-tenant deploy pipeline with OIDC into the Trax AWS
deployment account.
"""

from __future__ import annotations

import aws_cdk as cdk

from stacks.feature_store_stack import FeatureStoreStack


def main() -> None:
    app = cdk.App()

    tenants: list[str] = app.node.try_get_context("tenants") or ["aircanada"]

    for tenant_id in tenants:
        FeatureStoreStack(
            app,
            f"TraxIO-FeatureStore-{tenant_id}",
            tenant_id=tenant_id,
            description=(
                f"Trax IO Feature Store data-lake stack for tenant={tenant_id}. "
                "Phase 1 scaffold — Iceberg/Glue schemas, S3 lake, DynamoDB online "
                "layer, KMS envelope encryption."
            ),
        )

    # Project-wide resource tag per user instructions.
    cdk.Tags.of(app).add("Project", "TraxIO")

    app.synth()


if __name__ == "__main__":
    main()
