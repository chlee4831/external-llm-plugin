---
name: external-llm
description: Ask another AI model (OpenAI Codex, Google Antigravity/Gemini, or Claude) through its CLI and bring back its answer verbatim. Use when the user wants a second opinion from, a review by, or to delegate a question to a different model, e.g. "ask Codex", "get Gemini's view", "have the lightest Codex model check this".
---

# External model call

Runs a prompt through another model's CLI and returns its answer verbatim. Review the project's instructions and confirm the permitted material, tools and receiver before starting the external call.

From Claude Code, assign the task to the plugin's `external-llm` Agent. From Codex, assign it to a native subagent. Give the child the exact task, approved arguments and paths, and the conditions for using them. Only the child runs `xllm.py`. The parent keeps the host handle for status, the final result, a follow-up, or a stop request.

The child can run directly after receiving its assignment. A follow-up uses the same host child with the permitted context from the prior result and a new artifact directory. It starts a new external invocation. If the host cannot run a subagent, report the limitation to the parent.

## 1. Options

| Option | Values |
|---|---|
| `--provider` | `codex` (OpenAI), `agy` (Google Antigravity), `claude` (Anthropic) |
| `--model` | tier `lightest`, `light`, `standard`, `strongest`; family `luna`, `terra`, `sol`, `astra`, `flash`, `hydrogen`, `argon`, `carbon`, `haiku`, `sonnet`, `opus`, `fable`; or a full id |
| `--effort` | `low`, `medium`, `high`, `xhigh`, `max` |
| `--read-only` | no file edits; reading remains enabled |
| `--prompt-only` | Claude or Codex, with an empty `--cwd`; disable project loading, shell, web, MCP and hooks using the provider controls |
| `--exact-model` | pass the explicit `--model` and `--effort` unchanged |
| `--artifact-dir <dir>` | preserve response, stdout, stderr and status in a new absolute directory |
| `--cancel-file <file>` | stop the call when the absolute path is created |
| `--cwd <dir>` | project to work in (default: current directory) |
| `--timeout <sec>` | default 900 |
| `--max-chars <n>` | default 20000; a longer answer is cut and ends with the path of the full text |

- Use what the user gave; omit the rest. A tier sets a default effort (`scripts/tiers.json`).
- No provider named: never use your own. From Codex use `claude` or `agy`, from Claude `codex` or `agy`.
- `python <skill-dir>/scripts/xllm.py --list <provider>` shows the models.

## 2. Run

Use the absolute path of `scripts/xllm.py` with `python` or `python3`. The runner uses the installed, signed-in provider CLI. Follow the host's approvals for executing the command and contacting the provider.

Write the prompt to a temporary UTF-8 file with your file tool, then run once:

```
python <skill-dir>/scripts/xllm.py --provider <p> --prompt-file <file> --artifact-dir <new-dir> --cancel-file <cancel-path>
```

In a POSIX shell, a heredoc works instead of the file (`<<'XLLM_EOF'` … `XLLM_EOF`).

For prompt-only calls, include the permitted source material in the prompt. Keep the artifact directory outside the empty working directory.

## 3. Report

Report the status and original answer path to the parent once. `xllm-result.json` records execution status and the CLI exit code. After success, the parent returns the requested answer verbatim from `xllm-output.txt`. Keep both raw logs for verification. While the command is running, use observed execution status to answer the parent's requests.

On a stop request, create the assigned cancellation file while the runner is alive and confirm that it reports cancellation and process cleanup. If the host forcibly stops the child, check the cleanup separately. On failure, report the cause and artifact path to the parent.
