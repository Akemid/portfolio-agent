"""Single source of truth for the DynamoDB table schema tests build against.

Mirrors the key schema and TTL attribute design.md SS4.2 defines for the real
`portfolio-agent-sessions` table, so PR7's CDK `DataStack` table definition
can be checked against this one file instead of reverse-engineered from
scattered test setup code.
"""

from __future__ import annotations

from typing import Any

import boto3

from api.adapters.dynamo_keys import PARTITION_KEY, SORT_KEY, TTL_ATTRIBUTE

TABLE_NAME = "portfolio-agent-sessions"


def create_sessions_table(region_name: str = "us-east-1") -> Any:
    """Create the table and return a boto3 resource `Table` bound to it.

    MUST be called inside a `moto.mock_aws()` context — it issues a real
    `create_table` / `update_time_to_live` call against whatever DynamoDB
    endpoint boto3 resolves, which moto intercepts.
    """
    client = boto3.client("dynamodb", region_name=region_name)
    client.create_table(
        TableName=TABLE_NAME,
        AttributeDefinitions=[
            {"AttributeName": PARTITION_KEY, "AttributeType": "S"},
            {"AttributeName": SORT_KEY, "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": PARTITION_KEY, "KeyType": "HASH"},
            {"AttributeName": SORT_KEY, "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    client.update_time_to_live(
        TableName=TABLE_NAME,
        TimeToLiveSpecification={"Enabled": True, "AttributeName": TTL_ATTRIBUTE},
    )
    return boto3.resource("dynamodb", region_name=region_name).Table(TABLE_NAME)
