# Test results (2026-11-18)

Plan: `TESTPLAN.md`. Windows 11, codex 0.167.1, agy 1.5.5, Claude Code.

| Section | Result |
|---|---|
| 1 resolution, 2 error paths (`python -m unittest tests/test_xllm.py`) | 17/17 pass |
| 3 real calls (`bash tests/real_calls.sh <provider>`) | all pass: codex luna/low, claude haiku/low, agy hydrogen/low and argon/medium; agy strongest exits 2 naming carbon; Korean prompt with a spaced `--cwd` answered and followed AGENTS.md; no provider wrote x.txt; `--max-chars 20` truncates with the full-answer path |
| 4a Claude host, codex lightest | pass: one `external-llm:external-llm` agent on haiku, one Bash call, answer 144 from gpt-6-luna/low |
| 4b Claude host, agy strongest | pass: one call, failure passed back, no retry |
| 4c Codex host, no provider named | first run picked codex (fail); after SKILL.md names the providers to use from each host, it picked claude: `[model: claude/haiku/low]` 81 |
| 4d agy host, codex lightest | partial: the script ran and Codex answered 64 every time, but agy's main model relayed it unreliably. Run 1 wrote a `[model: codex/luna/low]` reply of its own before the subagent finished; run 2 was verbatim; run 3 ran the script three times and reworded the answer. agy's subagent call returns at once (async), and the main model then acts on its own. |

Not covered: real carbon calls (not on this plan); Codex host on another OS; a timeout against a real CLI (tested with a fake `.cmd` CLI that spawns a child: exit 124, child killed).
