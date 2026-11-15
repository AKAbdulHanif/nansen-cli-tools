#!/usr/bin/env bash
# Token Due Diligence — nine research sections, not nine guaranteed observations.
# Usage: ./token-due-diligence.sh <token_address> [--chain solana] [--days 7]
set -euo pipefail

TOKEN=""
CHAIN="solana"
DAYS=7
OUTPUT_DIR="./reports"
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
HELPER="$SCRIPT_DIR/lib/dd_support.py"

while [[ $# -gt 0 ]]; do
  case $1 in
    --chain|--days)
      if [[ $# -lt 2 || -z "$2" || "$2" == -* ]]; then
        printf 'Missing value for %s\n' "$1" >&2
        exit 1
      fi
      if [[ "$1" == --chain ]]; then CHAIN="$2"; else DAYS="$2"; fi
      shift 2;;
    -*) printf 'Unknown option: %s\n' "$1" >&2; exit 1;;
    *) TOKEN="$1"; shift;;
  esac
done

if [[ -z "$TOKEN" ]]; then
  printf 'Usage: %s <token_address> [--chain solana] [--days 7]\n' "$0" >&2
  exit 1
fi
# Canonical identifiers only; do not substitute or silently lowercase another chain.
if [[ ! "$CHAIN" =~ ^[a-z0-9-]+$ || ! "$TOKEN" =~ ^[a-zA-Z0-9]+$ || ! "$DAYS" =~ ^[1-9][0-9]*$ ]]; then
  printf 'INVALID_ARGUMENT: canonical lowercase chain, alphanumeric token address and positive days required\n' >&2
  exit 1
fi

for dependency in python3 nansen jq; do
  if ! command -v "$dependency" >/dev/null 2>&1; then
    printf 'MISSING_DEPENDENCY: %s; no research performed\n' "$dependency" >&2
    exit 2
  fi
done
# Global retirements fail before report creation or ANY Nansen invocation.
POLICY=$(python3 "$HELPER" policy --chain "$CHAIN") || exit $?
POLICY_DATE=$(printf '%s\n' "$POLICY" | jq -r '.policy_date')
mkdir -p "$OUTPUT_DIR"
TIMESTAMP=$(date -u +%Y%m%d_%H%M%S)
REPORT="$OUTPUT_DIR/dd-${CHAIN}-${TOKEN:0:8}-${TIMESTAMP}.md"
REQUESTS_EXECUTED=0

{
  printf '# Token Due Diligence Report\n'
  printf '_Token: `%s` | Chain: %s | Generated: %s_\n\n' "$TOKEN" "$CHAIN" "$(date -u '+%Y-%m-%d %H:%M UTC')"
  printf 'Retirement policy date: `%s` (policy only; not an observation timestamp).\n\n' "$POLICY_DATE"
} > "$REPORT"

collect_section() {
  local title="$1" kind="$2" endpoint="$3" result
  shift 3
  # The helper separates stdout/stderr, preserves sanitized evidence, checks success
  # and payload shape, and skips indicators ONLY on unsupported indicator chains.
  result=$(python3 "$HELPER" collect --chain "$CHAIN" --token "$TOKEN" \
    --kind "$kind" "$endpoint" "$@")
  if [[ $(printf '%s\n' "$result" | jq -r '.evidence.request_executed // false') == true ]]; then
    REQUESTS_EXECUTED=$((REQUESTS_EXECUTED + 1))
  fi
  {
    printf '## %s\n```json\n' "$title"
    printf '%s\n' "$result"
    printf '```\n\n'
  } >> "$REPORT"
}

# Keep canonical commands, supplied chain/token, windows, fields and limits.
# JSON values remain as returned: absent/null amounts are never rounded into zero.
collect_section '1. Token Overview' object info --pretty
collect_section '2. Nansen Indicators (specific token; not cross-token ranking)' indicators indicators --pretty
collect_section "3. Token Net Flows (${DAYS}d)" object flows --days "$DAYS" --pretty
collect_section '4. Flow Intelligence by Label' array flow-intelligence --days "$DAYS" --pretty
collect_section '5. Top Holders' array holders --limit 15 --fields address,label,balance_usd,percentage
collect_section '6. Recent Buyers & Sellers' array who-bought-sold --days "$DAYS" \
  --fields address,label,bought_usd,sold_usd --limit 10
collect_section '7. PnL Leaderboard' array pnl --days "$DAYS" \
  --fields address,label,realized_pnl_usd,unrealized_pnl_usd --limit 10
collect_section '8. Recent DEX Trades' array dex-trades --days 1 \
  --fields address,label,type,amount_usd --limit 15
collect_section '9. Price Action (OHLCV)' array ohlcv --timeframe 24h --limit 7

{
  printf '%s\n' '---'
  printf '**9 research sections assessed; %s CLI research requests executed.** Unavailable, empty and partial sections are not complete collected observations.\n\n' "$REQUESTS_EXECUTED"
  printf '_Built with Nansen CLI | #NansenCLI_\n'
} >> "$REPORT"
printf '\nDue diligence report saved: %s\n' "$REPORT"
cat "$REPORT"
