"""The JavaScript stream filter and the Python parser must agree on the payload.

Runs the real filter under Node against a block shaped like QuickNode's Solana
`block` dataset, then feeds what it returns to the backend's parser.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from app.quicknode.anomaly import parse_payload, usdc_outflows
from app.quicknode.payload import stream_item
from policy_fixtures import ATTACKER, NEWS_SELLER, addr
from scripts.quicknode_filter import render

USDC = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"
HOT = addr(10)
VAULT = addr(11)

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def bal(owner: str, amount: int, index: int) -> dict:
    return {
        "accountIndex": index, "mint": USDC, "owner": owner,
        "uiTokenAmount": {"amount": str(amount), "decimals": 6, "uiAmount": amount / 1e6, "uiAmountString": "x"},
    }


def tx(signature: str, pre: list, post: list, err=None) -> dict:
    return {
        "transaction": {"signatures": [signature], "message": {"accountKeys": []}},
        "meta": {"err": err, "fee": 5000, "preTokenBalances": pre, "postTokenBalances": post, "logMessages": []},
    }


BLOCK = {
    "blockTime": 1_794_000_000,
    "parentSlot": 399,
    "transactions": [
        tx("pay-news", [bal(HOT, 100_000, 1), bal(NEWS_SELLER, 0, 2)], [bal(HOT, 99_000, 1), bal(NEWS_SELLER, 1_000, 2)]),
        tx("stranger", [bal(addr(50), 5, 1)], [bal(addr(51), 5, 2)]),
        tx("drain", [bal(HOT, 99_000, 1)], [bal(HOT, 49_000, 1), bal(ATTACKER, 50_000, 3)]),
        tx("top-up", [bal(VAULT, 9_000_000, 1), bal(HOT, 49_000, 2)], [bal(VAULT, 8_900_000, 1), bal(HOT, 149_000, 2)]),
        tx("no-tokens", [], []),
    ],
}


def run_filter(stream: dict, watched: list[str]):
    script = render(watched) + "\nprocess.stdout.write(JSON.stringify(main(JSON.parse(require('fs').readFileSync(0, 'utf8')))));"
    result = subprocess.run(["node", "-e", script], input=json.dumps(stream), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_filter_keeps_only_transactions_touching_watched_owners():
    out = run_filter({"data": [BLOCK], "metadata": {}}, [HOT, VAULT])
    assert [t["signature"] for t in out["transactions"]] == ["pay-news", "drain", "top-up"]
    assert out["transactions"][0]["blockTime"] == 1_794_000_000
    assert out["transactions"][0]["slot"] == 400


def test_a_block_with_nothing_to_report_delivers_nothing():
    assert run_filter({"data": [BLOCK]}, [addr(99)]) is None
    assert run_filter({"data": [{"blockTime": 1, "transactions": []}]}, [HOT]) is None


def test_the_backend_reads_exactly_what_the_filter_emits():
    out = run_filter({"data": [BLOCK]}, [HOT, VAULT])

    parsed = parse_payload(out)
    flows = [flow for item in parsed for flow in usdc_outflows(item, mint=USDC, watched=frozenset({HOT}))]

    assert [(f.signature, f.amount_units, f.destinations) for f in flows] == [
        ("pay-news", 1_000, ((NEWS_SELLER, 1_000),)),
        ("drain", 50_000, ((ATTACKER, 50_000),)),
    ]


def test_the_local_relay_projection_matches_the_filter_for_the_same_transaction():
    out = run_filter({"data": [BLOCK]}, [HOT])
    from_filter = out["transactions"][0]
    source = BLOCK["transactions"][0]
    from_python = stream_item({**source, "slot": 400, "blockTime": BLOCK["blockTime"]})
    assert from_python == from_filter
