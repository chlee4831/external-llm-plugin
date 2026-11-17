#!/usr/bin/env bash
# TESTPLAN.md section 4: the plugin inside each host (network, uses quota).
# Usage: bash tests/host_e2e.sh <4a|4b|4c|4d|cleanup>
# 4c installs the plugin into Codex and 4d into agy; run "cleanup" afterwards.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLUGIN="$ROOT/plugins/external-llm"
W="$(mktemp -d)"; cd "$W"

summ_claude() { python -I -c '
import json, sys
sys.stdout.reconfigure(encoding="utf-8")
for line in open(sys.argv[1], encoding="utf-8"):
    try: e = json.loads(line)
    except ValueError: continue
    if e.get("subtype") == "init":
        print("agents:", [a for a in e.get("agents", []) if "external" in a])
    m = e.get("message") or {}
    if e.get("type") == "assistant":
        for c in m.get("content", []):
            if c.get("type") == "tool_use":
                print("sub" if e.get("parent_tool_use_id") else "main", m.get("model"), "tool:",
                      json.dumps(c.get("input"), ensure_ascii=False)[:220])
    if e.get("type") == "user" and e.get("parent_tool_use_id") is None:
        for c in m.get("content", []):
            if isinstance(c, dict) and c.get("type") == "tool_result":
                t = c.get("content"); t = t if isinstance(t, str) else json.dumps(t, ensure_ascii=False)
                print("subagent returned:", t[:300])
    if e.get("type") == "result":
        print("final:", (e.get("result") or "")[:300])
' "$1"; }

claude_case() {
  claude -p --plugin-dir "$PLUGIN" --model sonnet --strict-mcp-config --allowedTools "Bash(python *)" \
    --output-format stream-json --verbose "$1" > run.jsonl 2>&1
  echo "exit=$?"; summ_claude run.jsonl
}

case "$1" in
4a) claude_case "Use the external-llm subagent to ask Codex, lightest tier: what is 12*12? Report its reply verbatim." ;;
4b) claude_case "Use the external-llm subagent to ask agy (Gemini), strongest tier: what is 12*12? Report its reply verbatim." ;;
4c)
  codex plugin marketplace add "$ROOT" 2>&1 | tail -1
  codex plugin add external-llm@chlee-plugins 2>&1 | tail -1
  timeout 600 codex exec --skip-git-repo-check --ephemeral --color never --approve-for-me \
    -m gpt-6-luna -c 'model_reasoning_effort="low"' -o answer.txt \
    "Use the external-llm skill to get a second opinion from the lightest tier: what is 9*9? Report the script output verbatim." \
    > host.log 2>&1
  echo "exit=$?"; echo "commands:"; grep -aoE 'xllm\.py[^"]{0,120}' host.log | sort -u | head -5
  echo "final:"; cat answer.txt ;;
4d)
  timeout 120 agy plugin install "$PLUGIN" 2>&1 | tail -1
  timeout 600 agy -p="Use the external-llm subagent to ask Codex, lightest tier: what is 8*8? Report its reply verbatim." \
    --mode auto --model gemini-3.8-flash --effort low --output-format stream-json --print-timeout=500s > host.jsonl 2>&1
  echo "exit=$?"
  python -I -c '
import json, sys
sys.stdout.reconfigure(encoding="utf-8")
for line in open(sys.argv[1], encoding="utf-8"):
    try: e = json.loads(line)
    except ValueError: continue
    s = json.dumps(e, ensure_ascii=False)
    if "xllm.py" in s and e.get("event") != "result": print("call:", s[:260])
    if e.get("event") == "result": print("final:", (e.get("result") or {}).get("response", "")[:300])
' host.jsonl ;;
cleanup)
  codex plugin remove external-llm@chlee-plugins 2>&1 | tail -1
  codex plugin marketplace remove chlee-plugins 2>&1 | tail -1
  timeout 60 agy plugin uninstall external-llm 2>&1 | tail -1 ;;
esac
cd /; rm -rf "$W"
