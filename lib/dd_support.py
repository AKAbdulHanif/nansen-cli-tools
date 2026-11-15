#!/usr/bin/env python3
"""Offline policy and conservative JSON reporting for token-due-diligence.sh only."""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

GLOBAL_RETIREMENTS = {
    **dict.fromkeys(("algorand", "aptos", "chiliz", "gravity", "katana", "stacks",
                     "stellar", "viction", "mantra", "citrea", "ton", "sonic",
                     "plasma", "bitlayer"), "2026-11-25"),
    **dict.fromkeys(("optimism", "arc", "starknet", "sei", "mantle", "sui",
                     "avalanche"), "2027-02-01"),
}
INDICATOR_CUTOVER = "2026-12-04"
INDICATOR_CHAINS = frozenset(("ethereum", "solana", "bnb", "base"))
SENSITIVE_KEYS = re.compile(
    r"^(?:authorization|proxy.authorization|api.?key|access.?token|refresh.?token|"
    r"id.?token|secret|client.?secret|password|credential|credentials)$", re.I)


def policy_date(override=None):
    """Override is only a policy simulation date, never a market observation date."""
    value = override if override is not None else os.environ.get("NANSEN_POLICY_DATE")
    if value is None:
        return dt.datetime.now(dt.timezone.utc).date().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("NANSEN_POLICY_DATE must be a valid YYYY-MM-DD date")
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError("NANSEN_POLICY_DATE must be a valid YYYY-MM-DD date") from exc


def chain_policy(chain, today):
    retired_on = GLOBAL_RETIREMENTS.get(chain)
    retired = retired_on is not None and today >= retired_on
    return {"chain": chain, "policy_date": today, "retired_on": retired_on,
            "globally_retired": retired,
            "indicators_supported": not retired and (
                today < INDICATOR_CUTOVER or chain in INDICATOR_CHAINS)}


def sanitize(value, bounded=True):
    """Keep bounded diagnostic evidence without common credentials or control codes.

    This is defense in depth, not a guarantee for arbitrary secrets embedded in prose.
    Raw responses and stderr are never persisted by this helper.
    """
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if SENSITIVE_KEYS.fullmatch(str(k)) else sanitize(v, bounded)
                for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v, bounded) for v in (value[:100] if bounded else value)]
    if not isinstance(value, str):
        return value
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    value = re.sub(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+", "[REDACTED AUTH]", value)
    value = re.sub(
        r"(?i)\b(?:NANSEN_API_KEY|api[-_]?key|access[-_]?token|refresh[-_]?token|"
        r"client[-_]?secret|password|secret|authorization)\b[\"']?\s*[:=]\s*"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s&,;]+)", "[REDACTED CREDENTIAL]", value)
    value = re.sub(r"(?i)https?://[^\s/@:]+:[^\s/@]+@", "https://[REDACTED]@", value)
    value = "".join(c for c in value if c in "\n\t" or ord(c) >= 32)
    return value[:2000] if bounded else value


def reject_nonfinite(value):
    raise ValueError(f"Nonstandard JSON numeric constant: {value}")


def normalize(stdout, stderr, returncode, kind, chain, token):
    result = {"status": None, "chain": chain, "token_address": token,
              "data": None, "evidence": {"cli_exit_code": returncode}}
    if stderr.strip():
        result["evidence"]["stderr"] = sanitize(stderr)
    try:
        envelope = json.loads(stdout, parse_constant=reject_nonfinite)
    except (ValueError, TypeError):
        result["status"] = "REQUEST_FAILED" if returncode else "INVALID_JSON"
        result["reason"] = "CLI failed or returned invalid JSON; no observations inferred."
        if stdout.strip():
            result["evidence"]["stdout_excerpt"] = sanitize(stdout)
        return result
    if isinstance(envelope, dict):
        for key in ("error", "errors", "warning", "warnings", "message", "code"):
            if key in envelope:
                result["evidence"][key] = sanitize(envelope[key])
    if returncode:
        result["status"] = "REQUEST_FAILED"
        result["reason"] = "Nonzero CLI exit; response is not accepted as observations."
        return result
    if not isinstance(envelope, dict) or envelope.get("success") is not True:
        result["status"] = "API_ERROR" if isinstance(envelope, dict) and envelope.get("success") is False else "INVALID_ENVELOPE"
        result["reason"] = "Expected one JSON object with success=true."
        return result
    data = envelope.get("data")
    if data is None:
        result["status"] = "MISSING_DATA"
        result["reason"] = "Successful envelope has absent/null data, not a zero observation."
        return result
    expected_type = list if kind == "array" else dict
    if not isinstance(data, expected_type):
        result["status"] = "INVALID_DATA"
        result["reason"] = "Data shape differs from the expected endpoint payload; nesting is not guessed."
        return result
    if kind == "indicators":
        required = ("risk_indicators", "reward_indicators")
        missing = [key for key in required if key not in data or data[key] is None]
        if missing:
            result["status"] = "MISSING_DATA"
            result["missing_fields"] = missing
            result["reason"] = "Indicator arrays are absent/null; no score or zero risk inferred."
            return result
        if any(not isinstance(data[key], list) for key in required):
            result["status"] = "INVALID_DATA"
            result["reason"] = "Indicator fields must be arrays."
            return result
        empty = not any(data[key] for key in required)
    else:
        empty = not data
    result["status"] = "EMPTY_INDICATORS" if empty and kind == "indicators" else "EMPTY" if empty else "AVAILABLE"
    result["data"] = sanitize(data, bounded=False)
    if empty:
        result["reason"] = "Valid empty payload; no observations, risk assessment or score inferred."
    return result


def add_field_status(result, endpoint):
    fields = {
        "holders": ("balance_usd", "percentage"),
        "who-bought-sold": ("bought_usd", "sold_usd"),
        "pnl": ("realized_pnl_usd", "unrealized_pnl_usd"),
        "dex-trades": ("amount_usd",),
        "ohlcv": ("open", "high", "low", "close", "volume"),
    }.get(endpoint, ())
    if result["status"] == "AVAILABLE" and isinstance(result["data"], list):
        missing = []
        for index, row in enumerate(result["data"]):
            if not isinstance(row, dict):
                result.update(status="INVALID_DATA", data=None,
                              reason="Expected objects in the endpoint data array.")
                return result
            absent = [key for key in fields if row.get(key) is None]
            if absent:
                missing.append({"row": index, "fields": absent})
        if missing:
            result["status"] = "PARTIAL_DATA"
            result["missing_fields"] = missing
            result["reason"] = "Absent/null fields remain missing, never synthesized as zero."
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    policy = sub.add_parser("policy")
    policy.add_argument("--chain", required=True)
    collect = sub.add_parser("collect")
    collect.add_argument("--chain", required=True)
    collect.add_argument("--token", required=True)
    collect.add_argument("--kind", choices=("object", "array", "indicators"), required=True)
    collect.add_argument("endpoint")
    collect.add_argument("cli_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        today = policy_date()
    except ValueError as exc:
        print(f"INVALID_POLICY_DATE: {exc}; no research performed", file=sys.stderr)
        return 2
    state = chain_policy(args.chain, today)
    if args.action == "policy":
        if state["globally_retired"]:
            print(f"RETIRED_CHAIN: {args.chain}; retired on {state['retired_on']}; no research performed", file=sys.stderr)
            return 2
        print(json.dumps(state))
        return 0
    # Defense in depth: never invoke a globally retired or unsupported indicator request.
    if state["globally_retired"] or (args.endpoint == "indicators" and not state["indicators_supported"]):
        result = {"status": "RETIRED_CHAIN" if state["globally_retired"] else "UNSUPPORTED_CHAIN",
                  "chain": args.chain, "token_address": args.token, "data": None,
                  "reason": "Request skipped by retirement policy; no chain substituted.",
                  "evidence": {"request_executed": False}}
    else:
        try:
            completed = subprocess.run(
                ["nansen", "research", "token", args.endpoint, "--chain", args.chain,
                 "--token", args.token, *args.cli_args], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120, check=False)
            result = add_field_status(normalize(completed.stdout, completed.stderr,
                                                completed.returncode, args.kind,
                                                args.chain, args.token), args.endpoint)
            result["evidence"]["request_executed"] = True
        except (OSError, subprocess.TimeoutExpired) as exc:
            result = {"status": "REQUEST_FAILED", "chain": args.chain,
                      "token_address": args.token, "data": None,
                      "reason": "CLI unavailable or timed out; no observations inferred.",
                      "evidence": {"error": sanitize(str(exc)), "cli_exit_code": None,
                                   "request_executed": isinstance(exc, subprocess.TimeoutExpired)}}
            if isinstance(exc, subprocess.TimeoutExpired):
                for key, value in (("stderr", exc.stderr), ("stdout_excerpt", exc.stdout)):
                    if value:
                        if isinstance(value, bytes):
                            value = value.decode("utf-8", errors="replace")
                        result["evidence"][key] = sanitize(value)
    result["endpoint"] = args.endpoint
    result["policy_date"] = today
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
