import datetime as dt
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("dd_support", ROOT / "lib/dd_support.py")
SUPPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUPPORT)
ENDPOINTS = ["info", "indicators", "flows", "flow-intelligence", "holders",
             "who-bought-sold", "pnl", "dex-trades", "ohlcv"]


def normalized(data, kind="object", **extra):
    return SUPPORT.normalize(json.dumps({"success": True, "data": data, **extra}),
                             "", 0, kind, "ethereum", "FixtureToken")


class PolicyTests(unittest.TestCase):
    def test_november_boundary_all_fourteen(self):
        chains = [c for c, d in SUPPORT.GLOBAL_RETIREMENTS.items() if d == "2026-11-25"]
        self.assertEqual(len(chains), 14)
        for chain in chains:
            with self.subTest(chain=chain):
                self.assertFalse(SUPPORT.chain_policy(chain, "2026-11-24")["globally_retired"])
                self.assertTrue(SUPPORT.chain_policy(chain, "2026-11-25")["globally_retired"])

    def test_february_boundary_all_seven(self):
        chains = [c for c, d in SUPPORT.GLOBAL_RETIREMENTS.items() if d == "2027-02-01"]
        self.assertEqual(len(chains), 7)
        for chain in chains:
            with self.subTest(chain=chain):
                self.assertFalse(SUPPORT.chain_policy(chain, "2027-01-31")["globally_retired"])
                self.assertTrue(SUPPORT.chain_policy(chain, "2027-02-01")["globally_retired"])

    def test_december_boundary_four_retained_and_arbitrum(self):
        for chain in ("ethereum", "solana", "bnb", "base", "arbitrum"):
            with self.subTest(chain=chain):
                self.assertTrue(SUPPORT.chain_policy(chain, "2026-12-03")["indicators_supported"])
                self.assertEqual(SUPPORT.chain_policy(chain, "2026-12-04")["indicators_supported"],
                                 chain != "arbitrum")
                self.assertFalse(SUPPORT.chain_policy(chain, "2027-02-01")["globally_retired"])

    def test_global_schedule_also_blocks_indicators_before_december(self):
        self.assertFalse(SUPPORT.chain_policy("ton", "2026-11-25")["indicators_supported"])

    def test_override_is_exact_iso_date(self):
        self.assertEqual(SUPPORT.policy_date("2026-11-24"), "2026-11-24")
        for value in ("", "20261124", "2026-02-30", "2026-1-31", "tomorrow", "2026-12-04\n"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SUPPORT.policy_date(value)

    def test_environment_override(self):
        with patch.dict(os.environ, {"NANSEN_POLICY_DATE": "2027-02-01"}):
            self.assertEqual(SUPPORT.policy_date(), "2027-02-01")

    def test_default_uses_utc(self):
        instant = dt.datetime(2026, 11, 24, 23, 59, tzinfo=dt.timezone.utc)
        with patch.dict(os.environ, {}, clear=True), patch.object(SUPPORT.dt, "datetime") as clock:
            clock.now.return_value = instant
            self.assertEqual(SUPPORT.policy_date(), "2026-11-24")
            clock.now.assert_called_once_with(dt.timezone.utc)


class ResponseTests(unittest.TestCase):
    def test_failure_exit_keeps_sanitized_error_and_warning(self):
        response = json.dumps({"success": False, "error": {"code": "DENIED", "message": "api_key=fixture-secret"},
                               "warnings": ["coverage warning"]})
        result = SUPPORT.normalize(response, "Authorization: Bearer fixture-auth", 17,
                                   "indicators", "arbitrum", "FixtureToken")
        self.assertEqual(result["status"], "REQUEST_FAILED")
        self.assertIsNone(result["data"])
        self.assertEqual(result["evidence"]["error"]["code"], "DENIED")
        self.assertEqual(result["evidence"]["warnings"], ["coverage warning"])
        self.assertEqual(result["evidence"]["cli_exit_code"], 17)
        self.assertNotIn("fixture-secret", json.dumps(result))
        self.assertNotIn("fixture-auth", json.dumps(result))

    def test_zero_exit_error_json_is_not_success_or_empty(self):
        result = SUPPORT.normalize('{"success":false,"error":{"code":"BAD"}}', "", 0,
                                   "array", "ethereum", "FixtureToken")
        self.assertEqual(result["status"], "API_ERROR")
        self.assertIsNone(result["data"])

    def test_nonzero_exit_success_json_still_failed(self):
        result = SUPPORT.normalize('{"success":true,"data":{"value":0}}', "", 1,
                                   "object", "ethereum", "FixtureToken")
        self.assertEqual(result["status"], "REQUEST_FAILED")
        self.assertIsNone(result["data"])

    def test_invalid_json_keeps_sanitized_evidence(self):
        result = SUPPORT.normalize("not JSON api_key=fixture-secret", "warning", 0,
                                   "object", "ethereum", "FixtureToken")
        self.assertEqual(result["status"], "INVALID_JSON")
        self.assertIn("not JSON", result["evidence"]["stdout_excerpt"])
        self.assertNotIn("fixture-secret", json.dumps(result))

    def test_empty_stdout_is_invalid_not_empty_observations(self):
        self.assertEqual(SUPPORT.normalize("", "", 0, "array", "base", "FixtureToken")["status"], "INVALID_JSON")

    def test_missing_or_false_success_is_not_accepted(self):
        for response in ('{"data":[]}', '[]', 'null', '{"success":null,"data":[]}'):
            with self.subTest(response=response):
                self.assertEqual(SUPPORT.normalize(response, "", 0, "array", "base", "FixtureToken")["status"], "INVALID_ENVELOPE")

    def test_nonfinite_json_cannot_become_available(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                result = SUPPORT.normalize('{"success":true,"data":{"amount":' + value + '}}',
                                           "", 0, "object", "base", "FixtureToken")
                self.assertEqual(result["status"], "INVALID_JSON")

    def test_missing_and_null_data_are_missing(self):
        self.assertEqual(normalized(None)["status"], "MISSING_DATA")
        result = SUPPORT.normalize('{"success":true}', "", 0, "object", "base", "FixtureToken")
        self.assertEqual(result["status"], "MISSING_DATA")
        self.assertIsNone(result["data"])

    def test_truly_empty_array_and_object_are_explicit(self):
        for data, kind in (([], "array"), ({}, "object")):
            with self.subTest(kind=kind):
                result = normalized(data, kind)
                self.assertEqual(result["status"], "EMPTY")
                self.assertEqual(result["data"], data)

    def test_zero_and_null_are_not_defaulted(self):
        data = {"flow": 0, "balance": None}
        result = normalized(data)
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["data"], data)
        self.assertNotIn("invented", result["data"])

    def test_indicators_empty_are_not_zero_risk(self):
        result = normalized({"risk_indicators": [], "reward_indicators": [], "metadata": None}, "indicators")
        self.assertEqual(result["status"], "EMPTY_INDICATORS")
        self.assertIsNone(result["data"]["metadata"])
        self.assertNotIn("score", result)

    def test_indicators_missing_fields_are_not_empty(self):
        for data in ({}, {"risk_indicators": []}, {"risk_indicators": [], "reward_indicators": None}):
            with self.subTest(data=data):
                result = normalized(data, "indicators")
                self.assertEqual(result["status"], "MISSING_DATA")
                self.assertIsNone(result["data"])
                self.assertTrue(result["missing_fields"])

    def test_indicators_valid_zero_score_is_retained(self):
        data = {"risk_indicators": [{"score": 0}], "reward_indicators": []}
        result = normalized(data, "indicators", warnings=["limited coverage"])
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["data"]["risk_indicators"][0]["score"], 0)
        self.assertEqual(result["evidence"]["warnings"], ["limited coverage"])

    def test_wrong_shape_and_nested_array_not_guessed(self):
        for data, kind in (([], "object"), ({"data": []}, "array"), ("bad", "array"),
                           ({"risk_indicators": {}, "reward_indicators": []}, "indicators")):
            with self.subTest(data=data):
                self.assertEqual(normalized(data, kind)["status"], "INVALID_DATA")

    def test_partial_amounts_are_explicit_never_zero(self):
        result = SUPPORT.add_field_status(normalized([{"balance_usd": None}], "array"), "holders")
        self.assertEqual(result["status"], "PARTIAL_DATA")
        self.assertEqual(result["missing_fields"], [{"row": 0, "fields": ["balance_usd", "percentage"]}])
        self.assertEqual(result["data"], [{"balance_usd": None}])

    def test_all_selected_numeric_fields_accept_actual_zero(self):
        for endpoint, fields in (("holders", ["balance_usd", "percentage"]),
                                 ("who-bought-sold", ["bought_usd", "sold_usd"]),
                                 ("pnl", ["realized_pnl_usd", "unrealized_pnl_usd"]),
                                 ("dex-trades", ["amount_usd"]),
                                 ("ohlcv", ["open", "high", "low", "close", "volume"])):
            with self.subTest(endpoint=endpoint):
                data = [{field: 0 for field in fields}]
                result = SUPPORT.add_field_status(normalized(data, "array"), endpoint)
                self.assertEqual(result["status"], "AVAILABLE")
                self.assertEqual(result["data"], data)

    def test_nonobject_array_row_is_invalid(self):
        result = SUPPORT.add_field_status(normalized([0], "array"), "holders")
        self.assertEqual(result["status"], "INVALID_DATA")
        self.assertIsNone(result["data"])

    def test_nested_sensitive_keys_and_control_codes_removed(self):
        value = {"api_key": "fixture-secret", "error": {"access_token": "fixture-auth"},
                 "message": "\x1b[31mwarning\x00 password=fixture-password"}
        clean = SUPPORT.sanitize(value)
        self.assertNotIn("fixture-", json.dumps(clean))
        self.assertIn("warning", clean["message"])
        self.assertNotIn("\x1b", clean["message"])

    def test_diagnostic_bounds_do_not_truncate_observations(self):
        self.assertEqual(len(SUPPORT.sanitize("x" * 3000)), 2000)
        result = normalized({"long": "x" * 3000, "rows": list(range(101))})
        self.assertEqual(len(result["data"]["long"]), 3000)
        self.assertEqual(len(result["data"]["rows"]), 101)


class HelperTests(unittest.TestCase):
    def invoke(self, chain, date, exception=None):
        argv = ["dd_support.py", "collect", "--chain", chain, "--token", "FixtureToken",
                "--kind", "indicators", "indicators", "--pretty"]
        output = io.StringIO()
        with patch.dict(os.environ, {"NANSEN_POLICY_DATE": date}), \
                patch("sys.argv", argv), patch("sys.stdout", output), \
                patch.object(SUPPORT.subprocess, "run", side_effect=exception) as cli:
            self.assertEqual(SUPPORT.main(), 0)
            return json.loads(output.getvalue()), cli

    def test_direct_helper_rejects_global_chain_without_cli(self):
        result, cli = self.invoke("ton", "2026-11-25")
        cli.assert_not_called()
        self.assertEqual(result["status"], "RETIRED_CHAIN")
        self.assertFalse(result["evidence"]["request_executed"])

    def test_direct_helper_skips_arbitrum_indicators_without_cli(self):
        result, cli = self.invoke("arbitrum", "2026-12-04")
        cli.assert_not_called()
        self.assertEqual(result["status"], "UNSUPPORTED_CHAIN")
        self.assertFalse(result["evidence"]["request_executed"])

    def test_timeout_keeps_sanitized_warning_and_counts_executed_process(self):
        exception = subprocess.TimeoutExpired("nansen", 120,
                                              output=b"api_key=fixture-secret",
                                              stderr=b"timeout warning password=fixture-password")
        result, cli = self.invoke("base", "2026-12-04", exception)
        cli.assert_called_once()
        self.assertEqual(result["status"], "REQUEST_FAILED")
        self.assertTrue(result["evidence"]["request_executed"])
        self.assertIn("timeout warning", result["evidence"]["stderr"])
        self.assertNotIn("fixture-secret", json.dumps(result))
        self.assertNotIn("fixture-password", json.dumps(result))

    def test_unavailable_cli_is_failed_not_empty(self):
        result, cli = self.invoke("base", "2026-12-04", FileNotFoundError("nansen"))
        cli.assert_called_once()
        self.assertEqual(result["status"], "REQUEST_FAILED")
        self.assertFalse(result["evidence"]["request_executed"])
        self.assertIsNone(result["data"])


class SmokeTests(unittest.TestCase):
    def run_dd(self, date, chain="arbitrum", mode="valid", target="indicators", days="7", extra=()):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        work = Path(temp.name)
        bin_dir = work / "bin"
        bin_dir.mkdir()
        shutil.copyfile(ROOT / "tests/stub_nansen.py", bin_dir / "nansen")
        (bin_dir / "nansen").chmod(0o755)
        log = work / "calls.jsonl"
        env = os.environ.copy()
        # Exclude actual credentials even though only the stub can execute.
        for key in tuple(env):
            if any(word in key.upper() for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")):
                env.pop(key)
        env.update(PATH=f"{bin_dir}:{env['PATH']}", NANSEN_POLICY_DATE=date,
                   STUB_CALL_LOG=str(log), STUB_MODE=mode, STUB_TARGET=target)
        completed = subprocess.run(["bash", str(ROOT / "token-due-diligence.sh"), "FixtureToken",
                                    *(["--chain", chain] if chain is not None else []),
                                    "--days", days, *extra],
                                   cwd=work, env=env, capture_output=True, text=True, timeout=30)
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        reports = list((work / "reports").glob("*.md"))
        report = reports[0].read_text() if reports else ""
        blocks = [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", report, re.S)]
        return completed, calls, report, {block["endpoint"]: block for block in blocks}

    def assert_calls(self, calls, chain, expected):
        self.assertEqual([call[2] for call in calls], expected)
        for call in calls:
            self.assertEqual(call[call.index("--chain") + 1], chain)
            self.assertEqual(call[call.index("--token") + 1], "FixtureToken")

    def test_default_solana_is_preserved_before_and_after_cutover(self):
        for date in ("2026-11-24", "2026-12-04", "2027-02-01"):
            with self.subTest(date=date):
                completed, calls, _, blocks = self.run_dd(date, chain=None)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assert_calls(calls, "solana", ENDPOINTS)
                self.assertEqual(blocks["indicators"]["status"], "AVAILABLE")

    def test_november_24_still_calls_original_chain(self):
        completed, calls, _, blocks = self.run_dd("2026-11-24", "ton")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assert_calls(calls, "ton", ENDPOINTS)
        self.assertEqual(blocks["indicators"]["status"], "AVAILABLE")

    def test_november_25_no_cli_or_report(self):
        completed, calls, report, _ = self.run_dd("2026-11-25", "ton")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("RETIRED_CHAIN", completed.stderr)
        self.assertEqual(calls, [])
        self.assertEqual(report, "")

    def test_january_31_other_eligible_sections_continue(self):
        completed, calls, _, blocks = self.run_dd("2027-01-31", "optimism")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assert_calls(calls, "optimism", [e for e in ENDPOINTS if e != "indicators"])
        self.assertEqual(blocks["indicators"]["status"], "UNSUPPORTED_CHAIN")

    def test_february_1_no_cli_or_report(self):
        completed, calls, report, _ = self.run_dd("2027-02-01", "optimism")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("RETIRED_CHAIN", completed.stderr)
        self.assertEqual(calls, [])
        self.assertEqual(report, "")

    def test_december_3_all_four_and_arbitrum_execute_indicators(self):
        for chain in ("ethereum", "solana", "bnb", "base", "arbitrum"):
            with self.subTest(chain=chain):
                completed, calls, _, blocks = self.run_dd("2026-12-03", chain)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assert_calls(calls, chain, ENDPOINTS)
                self.assertEqual(blocks["indicators"]["status"], "AVAILABLE")

    def test_december_4_four_retained_execute_indicators(self):
        for chain in ("ethereum", "solana", "bnb", "base"):
            with self.subTest(chain=chain):
                completed, calls, _, blocks = self.run_dd("2026-12-04", chain)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assert_calls(calls, chain, ENDPOINTS)
                self.assertEqual(blocks["indicators"]["data"]["reward_indicators"][0]["score"], 0)

    def test_december_4_arbitrum_skips_only_indicators(self):
        completed, calls, report, blocks = self.run_dd("2026-12-04", "arbitrum", days="13")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assert_calls(calls, "arbitrum", [e for e in ENDPOINTS if e != "indicators"])
        self.assertEqual(blocks["indicators"]["status"], "UNSUPPORTED_CHAIN")
        self.assertFalse(blocks["indicators"]["evidence"]["request_executed"])
        self.assertIsNone(blocks["indicators"]["data"])
        self.assertIn("8 CLI research requests executed", report)
        self.assertNotIn("9 data points collected", report)
        self.assertIn("Generated: " + dt.datetime.now(dt.timezone.utc).date().isoformat(), report)
        for call in calls:
            if call[2] in ("flows", "flow-intelligence", "who-bought-sold", "pnl"):
                self.assertEqual(call[call.index("--days") + 1], "13")
        self.assertEqual(blocks["flows"]["data"]["observed_at"], "2026-01-01T00:00:00Z")

    def test_stub_failure_json_missing_empty_zero_and_partial(self):
        for mode, expected in (("failure", "REQUEST_FAILED"), ("error_json", "API_ERROR"),
                               ("invalid_json", "INVALID_JSON"), ("missing", "MISSING_DATA"),
                               ("null", "MISSING_DATA"), ("empty", "EMPTY_INDICATORS"),
                               ("partial", "MISSING_DATA"), ("wrong_shape", "INVALID_DATA"),
                               ("valid", "AVAILABLE")):
            with self.subTest(mode=mode):
                completed, calls, report, blocks = self.run_dd("2026-12-04", "ethereum", mode)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assert_calls(calls, "ethereum", ENDPOINTS)
                self.assertEqual(blocks["indicators"]["status"], expected)
                self.assertEqual(blocks["flows"]["status"], "AVAILABLE")
                self.assertNotIn("fixture-secret", report)
                self.assertNotIn("fixture-auth", report)
                if mode in ("failure", "error_json"):
                    self.assertEqual(blocks["indicators"]["evidence"]["error"]["code"], "FIXTURE_ERROR")
                    self.assertIn("fixture diagnostic", blocks["indicators"]["evidence"]["stderr"])

    def test_all_array_sections_missing_empty_partial_zero(self):
        for endpoint in ("holders", "who-bought-sold", "pnl", "dex-trades", "ohlcv"):
            for mode, status in (("missing", "MISSING_DATA"), ("empty", "EMPTY"),
                                 ("partial", "PARTIAL_DATA"), ("valid", "AVAILABLE")):
                with self.subTest(endpoint=endpoint, mode=mode):
                    completed, _, _, blocks = self.run_dd("2026-12-04", "base", mode, endpoint)
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(blocks[endpoint]["status"], status)
                    if mode == "partial":
                        self.assertEqual(blocks[endpoint]["data"], [{"address": "FixtureWallet", "balance_usd": None}])

    def test_invalid_policy_date_fails_before_cli(self):
        completed, calls, report, _ = self.run_dd("2026-02-30")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("INVALID_POLICY_DATE", completed.stderr)
        self.assertEqual(calls, [])
        self.assertEqual(report, "")

    def test_uppercase_chain_cannot_bypass_guard(self):
        completed, calls, report, _ = self.run_dd("2026-11-25", "TON")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("INVALID_ARGUMENT", completed.stderr)
        self.assertEqual(calls, [])
        self.assertEqual(report, "")

    def test_missing_argument_is_explicit(self):
        completed, calls, report, _ = self.run_dd("2026-12-03", extra=("--chain",))
        self.assertEqual(completed.returncode, 1)
        self.assertIn("Missing value for --chain", completed.stderr)
        self.assertEqual(calls, [])
        self.assertEqual(report, "")


if __name__ == "__main__":
    unittest.main()
