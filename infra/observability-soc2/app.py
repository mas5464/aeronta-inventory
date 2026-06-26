"""CDK app entrypoint for the Trax IO Observability + SOC 2 Control Plane.

Phase 2 scaffold: ``cdk synth`` only. No deploy targets are configured.
"""
from __future__ import annotations

import aws_cdk as cdk

from observability_soc2.stack import ObservabilitySoc2Stack
from observability_soc2.tenants import load_tenants_from_env

app = cdk.App()

# Tenants for which per-tenant KMS keys + log groups should be provisioned.
# Source of truth is observability_soc2.tenants; ``TRAX_IO_TENANTS_JSON`` in
# the environment overrides the in-code registry for CI.
tenants = load_tenants_from_env()

ObservabilitySoc2Stack(
    app,
    "TraxIoObservabilitySoc2Stack",
    tenants=tenants,
    env=cdk.Environment(region="us-east-1"),
    description=(
        "Trax IO Observability + SOC 2 Control Plane (sub-project #9, Phase 2). "
        "CloudTrail Lake, Audit Manager SOC 2 framework, per-tenant KMS keys, "
        "Object-Lock audit bucket, OTel collector placeholder, per-tenant log groups."
    ),
)

# Mandatory SOC 2 / ownership tags applied to every resource in every stack.
cdk.Tags.of(app).add("Project", "TraxIO")
cdk.Tags.of(app).add("Owner", "Platform+SecOps")
cdk.Tags.of(app).add("Compliance", "SOC2-TypeII")

app.synth()
