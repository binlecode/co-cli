#!/usr/bin/env bash
#
# Pretty-tail co-cli JSONL logs with color-coded spans and log records.
# Spans: agent (cyan), model (magenta), tool (yellow), with duration + status.
# Logs: color-coded by level.
#
# Usage: scripts/tail-jsonl.sh [-n N] [file]
#   -n N   Show last N lines then follow (default: follow from current end)
#   file   Path to JSONL file (default: ~/.co-cli/logs/co-cli.jsonl)

if ! command -v jq &>/dev/null; then
  echo "Error: jq is required" >&2
  exit 1
fi

RESET=$'\e[0m'
DIM=$'\e[2m'
CYAN=$'\e[36m'
MAGENTA=$'\e[35m'
YELLOW=$'\e[33m'
RED=$'\e[31m'
GREEN=$'\e[32m'

LAST=0

while getopts "n:" opt; do
  case $opt in
    n) LAST="$OPTARG" ;;
    *) echo "Usage: $0 [-n N] [file]" >&2; exit 1 ;;
  esac
done
shift $((OPTIND - 1))

FILE="${1:-$HOME/.co-cli/logs/co-cli.jsonl}"

if [[ ! -f "$FILE" ]]; then
  echo "Error: $FILE not found" >&2
  exit 1
fi

echo "${DIM}tailing $FILE — Ctrl+C to stop${RESET}"

tail -n "$LAST" -f "$FILE" | jq --unbuffered -r \
  --arg reset  "$RESET"   \
  --arg dim    "$DIM"     \
  --arg cyan   "$CYAN"    \
  --arg magenta "$MAGENTA" \
  --arg yellow "$YELLOW"  \
  --arg red    "$RED"     \
  --arg green  "$GREEN"   '
  def ts: .ts[11:23];

  def span_info:
    .name as $n |
    if   $n | startswith("invoke_agent")  then {type: "AGENT", color: $cyan,    label: ($n | ltrimstr("invoke_agent "))}
    elif $n | startswith("chat")          then {type: "MODEL", color: $magenta, label: ($n | ltrimstr("chat "))}
    elif $n | startswith("execute_tool")  then {type: "TOOL",  color: $yellow,  label: ($n | ltrimstr("execute_tool "))}
    else                                       {type: "SPAN",  color: "",       label: $n}
    end;

  def dur:
    if .duration_ms then " \($dim)\(.duration_ms | round)ms\($reset)" else "" end;

  def status_flag:
    if   .status == "ERROR" then " \($red)ERR\($reset)"
    elif .status == "OK"    then " \($green)OK\($reset)"
    else "" end;

  def level_color:
    if   .level == "ERROR"   then $red
    elif .level == "WARNING" then $yellow
    else $dim end;

  if .kind == "span" then
    span_info as $s |
    "\($dim)\(ts)\($reset)  \($s.color)\($s.type)\($reset)  \($s.label)\(status_flag)\(dur)"
  elif .kind == "log" then
    "\($dim)\(ts)\($reset)  \(level_color)\(.level)\($reset)  \($dim)\(.logger // "")\($reset)  \(.msg // "")"
  else empty end
'
