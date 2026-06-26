"""Fixtures for the DynamoDbOnlineStore tests.

Uses moto to mock DynamoDB in-memory (no Docker/AWS). The table mirrors the CDK key schema
(partition ``tenant_id``, sort ``pn_location``). Skips cleanly when boto3/moto aren't installed.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("boto3")
pytest.importorskip("moto")

import boto3  # noqa: E402
from moto import mock_aws  # noqa: E402

# moto intercepts boto3, but boto3 still needs *some* creds/region to build a client.
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

_TABLE = "trax-io-online"


@pytest.fixture
def online_table():
    """A moto-backed DynamoDB Table matching the CDK schema (tenant_id HASH, pn_location RANGE)."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName=_TABLE,
            KeySchema=[
                {"AttributeName": "tenant_id", "KeyType": "HASH"},
                {"AttributeName": "pn_location", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "tenant_id", "AttributeType": "S"},
                {"AttributeName": "pn_location", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table = ddb.Table(_TABLE)
        table.wait_until_exists()
        yield table
