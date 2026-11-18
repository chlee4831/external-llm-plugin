# external-llm

Ask another AI model (OpenAI Codex, Google Antigravity/Gemini, or Claude) through its CLI from inside Claude Code or Codex. The other model runs in the current project and follows its `AGENTS.md`.

```
.claude-plugin/marketplace.json      Claude Code marketplace
.agents/plugins/marketplace.json     Codex marketplace
plugins/external-llm/
  plugin.json                        portable manifest (Agent Plugins 1.0; Codex)
  .codex-plugin/plugin.json          Codex compatibility copy
  .claude-plugin/plugin.json         Claude Code manifest (points to claude/agents)
  skills/external-llm/SKILL.md       shared instructions for every host
  skills/external-llm/scripts/xllm.py    the CLI wrapper
  skills/external-llm/scripts/tiers.json tier -> model family map
  claude/agents/external-llm.md      Claude Code subagent (model: haiku)
```

## Models

`--model` takes a tier, a family name, or a full id. A tier resolves at call time to the newest model of its family.

| Tier | codex | claude | agy | default effort |
|---|---|---|---|---|
| `lightest` | luna | haiku | flash | low |
| `light` | terra | sonnet | hydrogen | low |
| `standard` | sol | opus | argon | medium |
| `strongest` | astra (else sol) | fable | carbon | high |

A tier whose family the account does not offer fails instead of switching models (agy `strongest` needs carbon). To change the map, edit `plugins/external-llm/skills/external-llm/scripts/tiers.json` or point `XLLM_TIERS` at your own copy. `python xllm.py --list <provider>` shows the available models and what each tier resolves to.

## Install

Requires Python 3 and whichever CLIs you want to call (`codex`, `agy`, `claude`), each already logged in. Each install is registered for your user account on that machine, so it applies to every project.

There are two ways to add the marketplace: from the GitHub repository, or from a local clone.

```bash
git clone https://github.com/chlee4831/external-llm-plugin.git   # local method only
```

| Host | From GitHub | From a local clone |
|---|---|---|
| Claude Code | `claude plugin marketplace add chlee4831/external-llm-plugin` | `claude plugin marketplace add ./external-llm-plugin` |
| Codex | `codex plugin marketplace add chlee4831/external-llm-plugin` | `codex plugin marketplace add ./external-llm-plugin` |

Then install the plugin from the marketplace:

```bash
claude plugin install external-llm@chlee-plugins
codex plugin add external-llm@chlee-plugins
```

To update: the GitHub method pulls the latest version on `claude plugin marketplace update` / `codex plugin marketplace upgrade`. The local method reads your clone, so `git pull` first. Codex copies the plugin at install time, so run its install command again after either update.

## Tests

`tests/TESTPLAN.md` lists the cases and `tests/RESULTS.md` the latest run. Offline: `python -m unittest tests/test_xllm.py`. Real calls: `bash tests/real_calls.sh <provider>` and `bash tests/host_e2e.sh <case>`.

## Notes

- Permissions follow Codex's defaults plus file writes, for every provider: the model may read, search the web, run shell commands and edit files, without asking. Each CLI keeps its other default tools, but none may start subagents, as a Claude Code subagent cannot.

  | Provider | Flags | Not blocked |
  |---|---|---|
  | Codex | `--sandbox workspace-write` (writes in the project and temp only; shell has no network), `features.multi_agent=false` | the subagent tools stay listed, but spawning fails |
  | agy | `--mode accept-edits --sandbox`, `permissions.allow` `command(*)`, `read_url(*)` | subagent tools cannot be switched off; the prompt asks the model not to use them |
  | Claude | `--permission-mode dontAsk`, `--allowedTools` Read, Grep, Glob, WebSearch, WebFetch, Bash, PowerShell, Edit, Write, NotebookEdit; `--disallowedTools` Task, Agent, Workflow | Claude has no shell sandbox |

- `--read-only` removes file edits: Codex `--sandbox read-only` (shell still runs, read-only), agy `--mode plan --sandbox`, Claude without the edit and shell tools. Claude's plan mode is avoided because it switches the model.
- In a sandboxed host (Codex on Windows), the script must run outside the sandbox. It needs network access and the user's CLI logins, so approve escalation when asked.