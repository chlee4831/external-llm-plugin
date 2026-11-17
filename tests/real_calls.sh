#!/usr/bin/env bash
# TESTPLAN.md section 3: real model calls (network, uses quota).
# Usage: bash tests/real_calls.sh <codex|agy|claude>
set -u
P="$1"
HERE="$(cd "$(dirname "$0")" && pwd)"
X="$HERE/../plugins/external-llm/skills/external-llm/scripts/xllm.py"
D="$(mktemp -d)/xllm test dir"          # path with a space (3e)
mkdir -p "$D"
printf 'Always end every answer with the line: -- verified by AGENTS.md\n' > "$D/AGENTS.md"

case "$P" in codex) M=lightest ;; claude) M=lightest ;; agy) M=light ;; esac

t() {  # name, expected-substring, then xllm args; prompt on stdin
  local name="$1" want="$2"; shift 2
  local out rc
  out="$(python "$X" "$@" 2>&1)"; rc=$?
  if [[ "$out" == *"$want"* ]]; then r=PASS; else r=FAIL; fi
  printf '%s %s/%s (exit %s)\n%s\n\n' "$r" "$P" "$name" "$rc" "$(printf '%s' "$out" | head -8)"
}

echo "Reply with the single word OK" | t 3a-ok "[model: $P/" --provider "$P" --model "$M"
if [ "$P" = agy ]; then
  echo "Reply with the single word OK" | t 3c-standard "[model: agy/gemini-4-argon/medium]" --provider agy --model standard
  echo "Reply with the single word OK" | t 3d-strongest "families tried: carbon" --provider agy --model strongest
fi
echo "한 단어로 '좋아요'라고만 답하세요." | t 3e3h-korean-agents "verified by AGENTS.md" --provider "$P" --model "$M" --cwd "$D"
echo "Create a file named x.txt containing hi in the current directory, then say DONE." \
  | t 3f-readonly "[model:" --provider "$P" --model "$M" --cwd "$D"
if [ -e "$D/x.txt" ]; then echo "FAIL $P/3f-readonly: x.txt was written"; else echo "PASS $P/3f-no-file"; fi
if [ "$P" = codex ]; then
  echo "Count from 1 to 40, separated by spaces, nothing else." | t 3g-truncate "[truncated; full answer:" --provider codex --model lightest --max-chars 20
fi
rm -rf "$(dirname "$D")"
