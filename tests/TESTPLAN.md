# external-llm test plan

`N` = calls a real model (network, uses quota). Everything else runs offline.

## 1. Model resolution (offline, `test_xllm.py`)

| # | Case | Expected |
|---|---|---|
| 1a | claude tiers | haiku/low, sonnet/low, opus/medium, fable/high |
| 1b | codex tiers with a fake model list | luna/terra/sol/astra slugs; strongest falls back to sol when astra is missing |
| 1c | codex tier with no model list (empty `CODEX_HOME`) | error "cannot list codex models", exit 2 |
| 1d | family `flash` with `x-flash-lite` listed before `x-flash` | `x-flash` |
| 1e | agy strongest without carbon | error naming `carbon` |
| 1f | unknown name without `-` / unknown id with `-` | error with `--list` hint / passed through |
| 1g | explicit effort over tier effort; effort the model does not list | explicit wins; nearest listed level; none when the model lists none |
| 1h | substring that is not a family part (`so`) | no match |

## 2. Error paths (offline, `test_xllm.py`)

| # | Case | Expected |
|---|---|---|
| 2a | empty prompt file | exit 2 "empty prompt" |
| 2b | cp949 prompt file | exit 2, no traceback |
| 2c | prompt file with UTF-8 BOM | BOM not in the prompt |
| 2d | `--cwd` that does not exist | exit 2, no traceback |
| 2e | broken JSON in `XLLM_TIERS` | exit 2, no traceback |
| 2f | CLI missing from PATH | exit 3 "not found", no leftover temp dir |
| 2g | `--provider foo`, `--effort ultra` | argparse exit 2 |
| 2h | timeout with a CLI that spawns a child and hangs | exit 124 after the deadline and cleanup, child killed, cli.log exists |
| 2i | CLI fails with partial output | nothing on stdout, partial answer on stderr |
| 2j | prompt-only Codex and Claude with a designated model and artifact directory | exact invocation arguments, terminal success evidence, original answer and hash, retained raw logs |
| 2k | prompt-only mismatched or blank response, wrong model, or tool use | failed outcome, no successful answer file |
| 2l | cancellation across two simultaneous invocations | selected invocation and descendants stop, unrelated invocation stays alive, final status and logs remain |
| 2m | stop file exists before launch or attempt directory already exists | no provider launch, prior artifacts remain |
| 2n | POSIX group probe or signal reports a permission error | keep cleanup unverified until the group is proved gone |
| 2o | POSIX CLI exits while a child is still running | ordinary mode succeeds and leaves the background child running; prompt-only mode waits briefly, then stops a residual child and reports failure |

## 3. Real calls (N, `real_calls.sh`)

Prompt: "Reply with the single word OK" unless noted.

| # | Case | Expected |
|---|---|---|
| 3a | codex lightest | `[model: codex/<luna>/low]`, OK, exit 0 |
| 3b | claude lightest, run from inside Claude Code | `[model: claude/haiku/low]`, OK |
| 3c | agy light / standard | hydrogen/low and argon/medium answer OK |
| 3d | agy strongest | exit 2 before any call, names carbon |
| 3e | Korean prompt, `--cwd` with a space, each provider | Korean answer intact |
| 3f | "Create file x.txt" | no file written, each provider |
| 3g | `--max-chars 20` | truncation marker and full-answer file |
| 3h | AGENTS.md in `--cwd` | answer follows it, each provider |

## 4. Hosts end to end (N)

| # | Case | Expected |
|---|---|---|
| 4a | Claude Code with `--plugin-dir`: "ask the lightest Codex model ..." | one `external-llm` agent (haiku); reply is the script stdout only |
| 4b | Claude Code: ask agy strongest | `[external-llm: failed]` plus stderr, no retry, no own answer |
| 4c | Codex host (plugin installed from the marketplace) | uses the skill, calls a non-codex provider, verbatim answer |
| 4d | agy host (plugin installed) | flash_lite agent runs the script once, verbatim answer |

Test installs in Codex and agy are removed afterwards.
