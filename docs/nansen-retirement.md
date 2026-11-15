# Token due diligence: retirement guards and missing-data integrity

This change is scoped to the actual `token-due-diligence.sh` entrypoint and its standard-library Python helper, `lib/dd_support.py`. The existing canonical `nansen research token indicators` command is retained. There is no top-token ranking replacement, synthetic score, chain substitution, wallet-asset removal, deployment or scheduler change.

## Date policy

Every due-diligence request is guarded against the global schedule. On and after **2026-11-25**, `algorand`, `aptos`, `chiliz`, `gravity`, `katana`, `stacks`, `stellar`, `viction`, `mantra`, `citrea`, `ton`, `sonic`, `plasma` and `bitlayer` are rejected before any Nansen invocation or report creation. On and after **2027-02-01**, the same applies to `optimism`, `arc`, `starknet`, `sei`, `mantle`, `sui` and `avalanche`.

On and after **2026-12-04**, specific-token indicators are requested only for **`ethereum`, `solana`, `bnb` and `base`**. For another globally eligible chain such as `arbitrum`, the indicators section reports `UNSUPPORTED_CHAIN` with `data: null` and `request_executed: false`; the other eight endpoint sections continue on the **original chain and token**. Their endpoint-specific support is not inferred from the indicator four-chain list. Before the relevant cutovers the policy does not narrow the original requests. Solana remains the default chain, seven days the default research window, and original endpoint options/limits are retained.

Production defaults to the actual **UTC calendar date**. For deterministic tests and offline policy simulations only, use an exact, valid `YYYY-MM-DD` environment override:

```bash
NANSEN_POLICY_DATE=2026-12-04 python3 lib/dd_support.py policy --chain arbitrum
python3 -m unittest discover -s tests -v
```

The first command is a **local policy lookup**, not a CLI/API request. Tests put a network-free executable stub named `nansen` first on `PATH`; they do not invoke the real CLI. Setting the override and running the real due-diligence script would still perform real research for eligible sections; the override is **not an offline mode** and must not be used as a workaround for server retirement. Invalid overrides fail closed. Do not set this override in production scheduling.

**The override changes policy evaluation only. It never changes the date of market observations, rewrites response timestamps, changes `--days`/`--timeframe`, or backdates the report's actual UTC generation timestamp.** It cannot restore an endpoint the server has retired. Each collection rechecks the policy before invoking the CLI, including across a UTC cutover.

## Report integrity

All nine due-diligence sections now use JSON status envelopes instead of silently defaulting absent amounts to zero or discarding parse errors. Original selected numeric values are retained without rounding or numeric defaults; actual zero remains zero and absent/null fields remain missing. Endpoint data shapes are checked conservatively: objects for overview/flows, arrays for the tabular endpoint families, and an object containing both indicator arrays for indicators. Alternative CLI nesting is not guessed.

| Status | Meaning |
| --- | --- |
| `AVAILABLE` | Success envelope and expected nonempty payload; not an independent certification of every field. |
| `PARTIAL_DATA` | Array observations exist but requested amount/price fields are absent/null. `missing_fields` identifies them; they are not zero-filled. |
| `EMPTY` | Successful, correctly shaped empty object or array. No observations, not inferred zero activity. |
| `EMPTY_INDICATORS` | Both indicator arrays are present and empty. No score, zero-risk assessment or cross-token ranking inferred. |
| `MISSING_DATA` | Absent/null payload or absent/null required indicator arrays. `data` is null. |
| `INVALID_JSON`, `INVALID_ENVELOPE`, `INVALID_DATA` | Invalid JSON, missing explicit success, or unexpected shape; no observations inferred. |
| `API_ERROR` | JSON explicitly says `success: false`, even when the CLI exits zero. |
| `REQUEST_FAILED` | Nonzero CLI exit, unavailable executable or timeout. A success-looking body on a nonzero exit is not accepted. |
| `UNSUPPORTED_CHAIN` | Indicators request deliberately skipped; other eligible endpoints continue. |
| `RETIRED_CHAIN` | Global policy rejection; the shell normally exits with code 2 before creating a report. |

Each section retains sanitized stdout error-envelope fields (`error`, `errors`, `code`, `message`, `warning`, `warnings`), separate stderr evidence, and CLI exit status when available. Invalid non-JSON stdout is retained only as a bounded, sanitized excerpt. Common authorization headers, credential assignments, sensitive JSON keys and control sequences are redacted. Diagnostic strings/lists are bounded; observation data is not silently truncated. Raw stdout/stderr are never written to disk by the helper. Redaction is defense in depth, **not a guarantee for unknown credentials embedded in arbitrary prose**; keep API keys out of diagnostic output and review reports before sharing.

The summary states nine sections assessed and the actual number of research processes executed, rather than claiming nine complete observations. Report sections are now JSON rather than rounded tabular text; consumers relying on the old Markdown formatting need adjustment. Each subprocess has a 120-second timeout; this is not an API retry mechanism. A failed/partial section does not stop independent eligible sections, so script exit zero means a report was generated, **not that all sections succeeded**. Consumers must inspect section statuses and evidence.

## Dependencies, tests and scope limitations

Due diligence requires Bash, `jq`, `python3` (3.8 or later) and an installed `nansen` CLI. Only the due-diligence entrypoint uses Python. The tests use Python's built-in `unittest`; no package install, API key, paid call or network is needed. They exercise November 24/25, December 3/4 and January 31/February 1 boundaries, all 14 + 7 global policy identifiers, retained four-chain indicators versus Arbitrum, absent/null versus valid empty payloads and true zero, error JSON with both nonzero and zero CLI exits, warnings/redaction, and unchanged request chains/tokens/windows and response observation dates.

No live CLI/API compatibility, account permissions, production host, scheduler, deployment or endpoint-specific support has been verified. The response assumptions are conservative checks based on the audited source/proposal, not a claim that every CLI version uses identical nesting. Smart Money scanners, whale mirror and all other shell scripts remain unchanged: their custom-chain loops and legacy missing-data behavior are **not** protected by this entrypoint's guard. No credential assignments in those scripts were read, reproduced, used or removed. Existing sample reports remain historical artifacts, not newly validated observations.

Official notices cited by the source audit:

- [Global chain retirement schedule](https://docs.nansen.ai/api/changelog/29-10-2026-chain-retirement)
- [Score retirement and specific-token indicator restriction](https://docs.nansen.ai/api/changelog/04-11-2026-nansen-score-retirement)
- [Specific-token indicators reference](https://docs.nansen.ai/api/token-god-mode/nansen-indicators)
