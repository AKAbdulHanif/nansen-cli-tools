#!/usr/bin/env python3
"""Test fixture: no network, credentials, SDK or real Nansen invocation."""
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["STUB_CALL_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")
assert args[:2] == ["research", "token"], args
endpoint = args[2]
mode = os.environ.get("STUB_MODE", "valid")
target = os.environ.get("STUB_TARGET", "indicators")
if endpoint != target:
    mode = "valid"

if mode in ("failure", "error_json"):
    print(json.dumps({"success": False, "error": {
        "code": "FIXTURE_ERROR", "message": "Request rejected api_key=fixture-secret"},
        "warnings": ["retry later"], "api_key": "fixture-secret"}))
    print("Authorization: Bearer fixture-auth; warning: fixture diagnostic", file=sys.stderr)
    sys.exit(17 if mode == "failure" else 0)
if mode == "invalid_json":
    print("not JSON api_key=fixture-secret")
    sys.exit(0)
if mode == "missing":
    print(json.dumps({"success": True, "warnings": ["coverage unavailable"]}))
    sys.exit(0)
if mode == "null":
    print(json.dumps({"success": True, "data": None}))
    sys.exit(0)

if endpoint == "indicators":
    data = {"risk_indicators": [], "reward_indicators": [{"score": 0}],
            "metadata": {"observed_at": "2026-01-01T00:00:00Z"}}
    if mode == "empty":
        data["reward_indicators"] = []
    if mode == "partial":
        del data["risk_indicators"]
elif endpoint in ("info", "flows"):
    data = {"address": "FixtureToken", "net_flow_usd": 0,
            "observed_at": "2026-01-01T00:00:00Z"}
    if mode == "empty":
        data = {}
else:
    data = [{"address": "FixtureWallet", "label": None, "balance_usd": 0,
             "percentage": 0, "bought_usd": 0, "sold_usd": 0,
             "realized_pnl_usd": 0, "unrealized_pnl_usd": 0,
             "amount_usd": 0, "date": "2026-01-01", "open": 0,
             "high": 0, "low": 0, "close": 0, "volume": 0}]
    if mode == "empty":
        data = []
    if mode == "partial":
        data = [{"address": "FixtureWallet", "balance_usd": None}]
if mode == "wrong_shape":
    data = "not the expected payload"
print(json.dumps({"success": True, "data": data, "warnings": ["fixture coverage warning"]}))
print("fixture stderr warning", file=sys.stderr)
