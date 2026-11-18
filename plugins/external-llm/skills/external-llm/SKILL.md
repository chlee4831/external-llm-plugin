---
name: external-llm
description: Ask another AI model (OpenAI Codex, Google Antigravity/Gemini, or Claude) through its CLI and bring back its answer verbatim. Use when the user wants a second opinion from, a review by, or to delegate a question to a different model, e.g. "ask Codex", "get Gemini's view", "have the lightest Codex model check this".
---

# External model call

Runs one prompt on another model's CLI in the current project and returns its final answer unchanged. That model follows the project's `AGENTS.md`. It may read, search the web, run shell commands and edit files, but cannot start subagents.

If an `external-llm` subagent is available, hand it the task and stop.

## 1. Options

| Option | Values |
|---|---|
| `--provider` | `codex` (OpenAI), `agy` (Google Antigravity), `claude` (Anthropic) |
| `--model` | tier `lightest`, `light`, `standard`, `strongest`; family `luna`, `terra`, `sol`, `astra`, `flash`, `hydrogen`, `argon`, `carbon`, `haiku`, `sonnet`, `opus`, `fable`; or a full id |
| `--effort` | `low`, `medium`, `high`, `xhigh`, `max` |
| `--read-only` | no file edits; use it when the user says not to change files |
| `--cwd <dir>` | project to work in (default: current directory) |
| `--timeout <sec>` | default 900 |
| `--max-chars <n>` | default 20000; a longer answer is cut and ends with the path of the full text |

- Use what the user gave; omit the rest. A tier sets a default effort (`scripts/tiers.json`).
- No provider named: never use your own. From Codex use `claude` or `agy`, from Claude `codex` or `agy`.
- `python <skill-dir>/scripts/xllm.py --list <provider>` shows the models.

## 2. Run

The script is `scripts/xllm.py` in this skill's directory; use its absolute path, and `python3` if `python` is missing. It needs network access and the user's CLI logins, so run it outside any sandbox; on a sandboxed host (e.g. Codex), ask for escalation, and rerun with escalation if a command is "not found".

Write the prompt to a temporary UTF-8 file with your file tool, then run once:

```
python <skill-dir>/scripts/xllm.py --provider <p> [--model <m>] [--effort <e>] --prompt-file <file>
```

In a POSIX shell, a heredoc works instead of the file (`<<'XLLM_EOF'` … `XLLM_EOF`).

The prompt carries everything the other model needs: question, file paths, constraints. Never your own answer.

## 3. Report

Print the script's stdout unchanged; its first line, `[model: provider/model/effort]`, names the model. Add nothing, and write nothing before the script finishes.

On a non-zero exit, report its stderr and stop. Do not retry.
