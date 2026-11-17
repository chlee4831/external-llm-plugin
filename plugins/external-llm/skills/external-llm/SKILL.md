---
name: external-llm
description: Ask another AI model (OpenAI Codex, Google Antigravity/Gemini, or Claude) through its CLI and bring back its answer verbatim. Use when the user wants a second opinion from, a review by, or to delegate a question to a different model, e.g. "ask Codex", "get Gemini's view", "have the lightest Codex model check this".
---

# External model call

Runs one prompt on another model's CLI, read-only, in the current project, and returns its final answer unchanged. The other model reads the project's `AGENTS.md` as it would on its own.

If an `external-llm` subagent is available, give it the task and stop here. Otherwise follow the steps below.

## 1. Choose provider, model, effort

| Option | Values |
|---|---|
| `--provider` | `codex` (OpenAI), `agy` (Google Antigravity), `claude` (Anthropic) |
| `--model` | a tier: `lightest`, `light`, `standard`, `strongest`; a family: `luna`, `terra`, `sol`, `astra`, `flash`, `hydrogen`, `argon`, `carbon`, `haiku`, `sonnet`, `opus`, `fable`; or a full model id |
| `--effort` | `low`, `medium`, `high`, `xhigh`, `max` |

- Use the provider, model and effort the user gave. Omit any they did not give.
- If the user named no provider, do not use your own: from Codex use `claude` or `agy`, from Antigravity use `codex` or `claude`, from Claude use `codex` or `agy`.
- A tier picks the newest model of its family and sets a default effort (`scripts/tiers.json`).
- To see the choices: `python <skill-dir>/scripts/xllm.py --list <provider>`.

## 2. Run the script

The script is `scripts/xllm.py` in this skill's directory. Use its absolute path, and `python3` if `python` is missing.

Run it outside any sandbox: it needs network access and the user's CLI login. On a sandboxed host (e.g. Codex), ask for escalation first. If `python`, `codex`, `agy` or `claude` is "not found" inside a sandbox, rerun the same command with escalation.

Write the prompt to a temporary UTF-8 file with your file tool, then run:

```
python <skill-dir>/scripts/xllm.py --provider <p> [--model <m>] [--effort <e>] --prompt-file <file>
```

In a POSIX shell you can pipe the prompt instead:

```bash
python <skill-dir>/scripts/xllm.py --provider codex --model lightest <<'XLLM_EOF'
<prompt>
XLLM_EOF
```

The prompt must carry everything the other model needs: the question, file paths, and constraints. Do not put your own answer in it.

Other options: `--cwd <dir>` (project to work in; default: current directory), `--timeout <sec>` (default 900), `--max-chars <n>` (default 20000; a longer answer is cut and ends with the path of the full text).

## 3. Report

Print the script's stdout unchanged. Its first line, `[model: provider/model/effort]`, says which model answered. Add nothing, and never write that line or an answer yourself before the script has finished.

If the script exits non-zero, report its stderr and stop. Do not retry.
