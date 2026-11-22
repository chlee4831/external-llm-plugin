#!/usr/bin/env python3
"""Run one prompt on an external model through its CLI and print the final answer.

Providers: codex (OpenAI Codex CLI), agy (Google Antigravity CLI), claude (Claude Code).
Every provider runs in the working directory (--cwd, default: the current
directory), so it reads that project's AGENTS.md like it would on its own.
Permissions follow Codex's defaults plus file writes: it may read, search the
web, run shell commands in the CLI's sandbox, and edit files, without asking;
each CLI keeps its default tools except subagents, which a Claude Code subagent
cannot start either (agy can only be asked not to). --read-only takes the file
writes away again.

The prompt comes from --prompt-file, or from stdin when no file is given. Only the
model's final answer goes to stdout. CLI progress is kept in a log file whose path
is printed to stderr on failure.

Examples:
    python xllm.py --provider codex --model astra --effort high --prompt-file q.txt
    python xllm.py --provider agy --model argon --effort low < q.txt
    python xllm.py --list codex
"""

import argparse
import functools
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

DEFAULT_TIMEOUT = 900
DEFAULT_MAX_CHARS = 20000
EFFORTS = ("low", "medium", "high", "xhigh", "max")
EFFORT_SUFFIX = re.compile(r"-(%s)$" % "|".join(EFFORTS))
POLL_INTERVAL = 0.2
STOP_GRACE = 5
STRICT_EXIT_GRACE = 1


class XllmError(Exception):
    pass


def find_exe(name):
    exe = shutil.which(name)
    if not exe:
        raise XllmError(f"{name} CLI not found on PATH")
    return exe


# ---------------------------------------------------------------- model lists

def codex_models():
    """Visible Codex models, most recommended first, from Codex's own cache."""
    home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    path = os.path.join(home, "models_cache.json")
    try:
        with open(path, encoding="utf-8") as f:
            models = json.load(f).get("models", [])
    except (OSError, ValueError):
        return []
    models = [m for m in models if m.get("visibility") != "hide"]
    models.sort(key=lambda m: m.get("priority", 999))
    return [(m["slug"], [e["effort"] for e in m.get("supported_reasoning_levels", [])])
            for m in models]


def agy_models():
    """agy model ids grouped by base name (effort suffix removed), in listed order."""
    out = subprocess.run([find_exe("agy"), "models"], capture_output=True, timeout=120)
    if out.returncode != 0:
        return []
    bases = {}
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        mid = line.split("\t", 1)[0].strip()
        if not mid or " " in mid or "\t" not in line:
            continue
        m = EFFORT_SUFFIX.search(mid)
        base, effort = (mid[: m.start()], m.group(1)) if m else (mid, None)
        bases.setdefault(base, [])
        if effort:
            bases[base].append(effort)
    return list(bases.items())


CLAUDE_MODELS = [(m, list(EFFORTS)) for m in ("opus", "sonnet", "haiku", "fable")]


@functools.lru_cache(maxsize=None)
def list_models(provider):
    return {"codex": codex_models, "agy": agy_models,
            "claude": lambda: CLAUDE_MODELS}[provider]()


def load_tiers():
    path = os.environ.get("XLLM_TIERS") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "tiers.json")
    with open(path, encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


def match_family(provider, models, name):
    """First listed model (lists run newest first) that has `name` as a
    hyphen-separated part, preferring ids that end with it (flash over flash-lite)."""
    if provider == "claude" or name in models:
        return name
    low = re.escape(name.lower())
    for pattern in (r"(^|-)%s$" % low, r"(^|-)%s(-|$)" % low):
        for m in models:
            if re.search(pattern, m.lower()):
                return m
    return None


def fit_effort(provider, model, wanted):
    """A tier's default effort, adjusted to what the model lists.

    No effort when the model lists none (the CLI rejects --effort then);
    the nearest listed level when the wanted one is missing.
    """
    if not wanted or provider == "claude":
        return wanted
    known = dict(list_models(provider)).get(model)
    if known is None:
        return wanted
    if not known:
        return None
    if wanted in known:
        return wanted
    rank = {e: i for i, e in enumerate(EFFORTS)}
    usable = [e for e in known if e in rank]
    if not usable or wanted not in rank:
        return wanted
    return min(usable, key=lambda e: abs(rank[e] - rank[wanted]))


def resolve_model(provider, name, effort):
    """Resolve a tier name, short family name, or full id to (model id, effort).

    Tier names (lightest, light, standard, strongest; see tiers.json) map to
    family keywords, so the newest model of that family is chosen at call time.
    The tier's effort applies only when no effort was given. Either effort is
    moved to the nearest level the model lists.
    """
    if not name:
        return None, effort
    tiers = load_tiers()
    if name.lower() in tiers:
        tier = dict(tiers[name.lower()])
        families = tier.get(provider) or []
        if isinstance(families, dict):        # per-provider effort override
            tier["effort"] = families.get("effort", tier.get("effort"))
            families = families.get("families", [])
        models = [] if provider == "claude" else [m for m, _ in list_models(provider)]
        if provider != "claude" and not models:
            raise XllmError(f"cannot list {provider} models, so tier '{name}' cannot be resolved; "
                            f"check that the {provider} CLI is installed and logged in")
        for fam in families:
            mid = match_family(provider, models, fam)
            if mid:
                return mid, fit_effort(provider, mid, effort or tier.get("effort"))
        raise XllmError(f"tier '{name}' has no available {provider} model "
                        f"(families tried: {', '.join(families) or 'none'})")
    models = [] if provider == "claude" else [m for m, _ in list_models(provider)]
    # With no model list to check against, pass the name through and let the CLI decide.
    mid = match_family(provider, models, name) if models or provider == "claude" else name
    if not mid and "-" in name:
        # Looks like a full id the list does not show (hidden or new): let the CLI decide.
        mid = name
    if not mid:
        raise XllmError(f"no {provider} model matches '{name}'; "
                        f"run with --list {provider} to see the choices")
    return mid, fit_effort(provider, mid, effort)


# ---------------------------------------------------------------- providers

def run_codex(prompt, a, work):
    out_file = os.path.join(work, "answer.txt")
    # Codex's defaults (no approvals, sandboxed shell, web search on) with the
    # sandbox widened to write in the project: workspace-write. --read-only
    # keeps exec's default read-only sandbox.
    cmd = [find_exe("codex"), "exec",
           "--sandbox", "read-only" if a.read_only or a.prompt_only else "workspace-write",
           "--skip-git-repo-check", "--ephemeral", "--color", "never",
           "-o", out_file,                    # runs in a.cwd (see call)
           # No subagents, as in a Claude Code subagent.
           "-c", "features.multi_agent=false"]
    if a.artifact_dir or a.prompt_only:
        cmd += ["--json"]
    if a.prompt_only:
        cmd += ["--ignore-user-config", "--strict-config",
                "-c", "project_root_markers=[]", "-c", "project_doc_max_bytes=0",
                "-c", "mcp_servers={}", "-c", 'web_search="disabled"',
                "-c", "features.hooks=false",
                "-c", "features.apps=false", "-c", "features.plugins=false",
                "-c", "features.shell_tool=false", "-c", "features.unified_exec=false",
                "-c", "features.shell_snapshot=false",
                "-c", "features.multi_agent_v2=false"]
    if a.model:
        cmd += ["-m", a.model]
    if a.effort:
        cmd += ["-c", f'model_reasoning_effort="{a.effort}"']
    cmd.append("-")                           # prompt on stdin
    rc, out = call(cmd, prompt.encode("utf-8"), a, work)
    answer = read_text(out_file)
    events = list(json_events(out))
    result = next((e for e in reversed(events) if e.get("type") == "turn.completed"), None)
    if result:
        a.result["usage"] = result.get("usage")
        a.result["observed_model"] = result.get("model")
    if a.prompt_only and rc == 0:
        if not result or any(e.get("type") in ("error", "turn.failed") for e in events):
            return 1, answer, "Codex did not report a successful turn"
        items = [e.get("item", {}) for e in events if e.get("type", "").startswith("item.")]
        if any(not isinstance(item, dict) or item.get("type") not in
               ("agent_message", "reasoning", "plan") for item in items):
            return 1, answer, "Codex reported a tool or unexpected item in prompt-only mode"
        final = next((item["text"] for item in reversed(items)
                      if item.get("type") == "agent_message" and "text" in item), "")
        if not isinstance(final, str) or final.strip() != answer.strip():
            return 1, answer, "Codex final message did not match the answer file"
    return rc, answer, None


AGY_NO_SUBAGENTS = ("(Do not use define_subagent, invoke_subagent, manage_subagents or "
                    "browser_subagent: do the task yourself.)")


def run_agy(prompt, a, work):
    cmd = [find_exe("agy"),
           # Like Codex: shell commands and web reads allowed, inside agy's
           # sandbox; edits accepted. Read-only: plan mode instead.
           "--mode", "plan" if a.read_only else "accept-edits", "--sandbox",
           "--settings", json.dumps({"permissions": {
               "allow": ["command(*)", "read_url(*)"]}}),
           "--input-format", "stream-json", "--output-format", "stream-json",
           f"--print-timeout={a.timeout}s"]
    if a.model:
        cmd += ["--model", a.model]
    if a.effort:
        cmd += ["--effort", a.effort]
    cmd.append("-p=")                         # prompt comes from stdin below
    # agy cannot switch its subagent tools off, so it is asked not to use them.
    content = AGY_NO_SUBAGENTS + "\n\n" + prompt
    msg = json.dumps({"event": "user", "message": {"content": content}}, ensure_ascii=False)
    rc, out = call(cmd, (msg + "\n").encode("utf-8"), a, work)
    result = last_event(out, lambda e: e.get("event") == "result")
    if not result:
        return rc or 1, "", "no result event in agy output"
    r = result.get("result", {})
    err = None if r.get("status") == "SUCCESS" else (r.get("error") or r.get("status"))
    return rc, (r.get("response") or "").strip(), err


def run_claude(prompt, a, work):
    # Like Codex: the default tools, with shell, web and (unless --read-only)
    # edits pre-approved; anything else that needs approval is denied.
    # Claude has no default sandbox, so --read-only drops the shell too.
    # (Plan mode is avoided: it switches the model to a different one.)
    tools = "Read,Grep,Glob,WebSearch,WebFetch" + (
        "" if a.read_only else ",Bash,PowerShell,Edit,Write,NotebookEdit")
    cmd = [find_exe("claude"), "-p", "--permission-mode", "dontAsk",
           "--no-session-persistence",
           "--output-format", "stream-json" if a.prompt_only else "json"]
    if a.prompt_only:
        cmd += ["--verbose", "--safe-mode", "--restricted", "--strict-mcp-config", "--tools", "",
                "--settings", '{"disableAllHooks":true}', "--permission-prompts", "none",
                "--no-chrome", "--disable-slash-commands"]
    else:
        cmd += ["--allowedTools", tools, "--disallowedTools", "Task,Agent,Workflow"]
    if a.model:
        cmd += ["--model", a.model]
    if a.effort:
        cmd += ["--effort", a.effort]
    rc, out = call(cmd, prompt.encode("utf-8"), a, work)
    events = list(json_events(out))
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if not result:
        return rc or 1, "", "no result in claude output"
    a.result["usage"] = result.get("usage")
    models = result.get("modelUsage") or {}
    a.result["observed_model"] = next(iter(models)) if isinstance(models, dict) and len(models) == 1 else None
    err = result.get("subtype") if result.get("is_error") else None
    if a.prompt_only and rc == 0:
        init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)
        if not init or init.get("tools") != [] or init.get("mcp_servers") != []:
            err = "Claude did not report an empty tool inventory"
        elif result.get("subtype") != "success" or result.get("permission_denials"):
            err = "Claude did not report a successful tool-free turn"
        a.result["observed_model"] = init.get("model") if init else a.result["observed_model"]
    return rc, result.get("result") or "", err


PROVIDERS = {"codex": run_codex, "agy": run_agy, "claude": run_claude}


# ---------------------------------------------------------------- helpers

def posix_group_exists(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The group is not proved absent. Darwin can briefly reject this probe
        # while only a zombie remains in the group.
        return True
    return True


def wait_posix_group(pid, grace):
    deadline = time.monotonic() + grace
    while posix_group_exists(pid):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(POLL_INTERVAL, remaining))
    return True


def kill_tree(proc):
    """Stop the CLI's own process tree and reap the directly launched process."""
    if os.name == "nt":
        # A Windows .cmd launcher can have a Node CLI underneath it.
        try:
            result = subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                                    capture_output=True, timeout=STOP_GRACE)
            requested = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            requested = False
        if not requested and proc.poll() is None:
            proc.kill()
    else:
        requested = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            requested = False
            if proc.poll() is None:
                try:
                    proc.kill()
                except PermissionError:
                    pass
    try:
        proc.wait(timeout=STOP_GRACE)
    except subprocess.TimeoutExpired:
        return False, False
    if os.name == "nt":
        # taskkill confirms the request, not that the rest of the tree is gone.
        return requested, None
    return requested, wait_posix_group(proc.pid, STOP_GRACE)


def write_result(a, work, **updates):
    a.result.update(updates)
    if a.artifact_dir:
        path = os.path.join(work, "xllm-result.json")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(a.result, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(path + ".tmp", path)


def cancellation_requested(a):
    return a.interrupted or (a.cancel_file and os.path.exists(a.cancel_file))


def call(cmd, stdin_bytes, a, work):
    """Run the CLI with file-backed I/O so cancellation can be polled."""
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)               # allow nesting Claude Code
    stdout = os.path.join(work, "xllm-stdout.log")
    stderr = os.path.join(work, "xllm-stderr.log")
    write_result(a, work, command=cmd)
    with tempfile.TemporaryFile(dir=work) as stdin, open(stdout, "wb") as out, open(stderr, "wb") as err:
        if cancellation_requested(a):
            write_result(a, work, status="cancelled", cleanup_verified=True)
            return 130, ""
        stdin.write(stdin_bytes)
        stdin.seek(0)
        proc = subprocess.Popen(cmd, cwd=a.cwd, env=env, stdin=stdin, stdout=out, stderr=err,
                                start_new_session=(os.name != "nt"))
        deadline = time.monotonic() + a.timeout
        status = "running"
        requested = None
        cleanup = None
        try:
            write_result(a, work, pid=proc.pid, pgid=proc.pid if os.name != "nt" else None)
            while proc.poll() is None:
                if cancellation_requested(a):
                    status = "cancelled"
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    status = "timed_out"
                    break
                time.sleep(min(POLL_INTERVAL, remaining))
        finally:
            active = proc.poll() is None
            if active or status in ("cancelled", "timed_out"):
                requested, cleanup = kill_tree(proc)
            elif os.name != "nt":
                grace = STRICT_EXIT_GRACE if a.prompt_only else 0
                cleanup = wait_posix_group(proc.pid, grace)
                if a.prompt_only and not cleanup:
                    requested, cleanup = kill_tree(proc)
                    status = "failed"
                    a.result["error"] = "CLI exited with a residual process"
            write_result(a, work, status=status, exit_code=proc.returncode,
                         cleanup_requested=requested, cleanup_verified=cleanup)
    with open(stdout, "rb") as f:
        out_data = f.read()
    if not a.artifact_dir:
        with open(stderr, "rb") as f:
            write_log(work, cmd, out_data, f.read())
    rc = {"cancelled": 130, "timed_out": 124, "failed": 1}.get(status, proc.returncode)
    return rc, out_data.decode("utf-8", "replace")


def write_log(work, cmd, out, err):
    with open(os.path.join(work, "cli.log"), "w", encoding="utf-8") as log:
        log.write("$ " + " ".join(cmd) + "\n" + out.decode("utf-8", "replace")
                  + "\n--- stderr\n" + err.decode("utf-8", "replace"))


def json_events(text):
    for line in text.splitlines():
        if not line.lstrip().startswith("{"):
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue


def last_event(text, pred):
    return next((e for e in reversed(list(json_events(text))) if pred(e)), None)


def read_text(path):
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        return f.read()


def run_attempt(prompt, a, work):
    label = a.provider + (f"/{a.model}" if a.model else "") + (f"/{a.effort}" if a.effort else "")
    log = os.path.join(work, "xllm-stderr.log" if a.artifact_dir else "cli.log")
    try:
        rc, answer, err = PROVIDERS[a.provider](prompt, a, work)
    except (XllmError, OSError) as e:
        write_result(a, work, status="failed", error=str(e))
        if not a.artifact_dir:
            shutil.rmtree(work, ignore_errors=True)
        print(f"xllm: {e}", file=sys.stderr)
        return 3

    status = a.result["status"]
    if status in ("cancelled", "timed_out"):
        cause = "was cancelled" if status == "cancelled" else f"timed out after {a.timeout}s"
        if a.result["cleanup_verified"] is not True:
            cause += "; process cleanup could not be verified"
        print(f"xllm: {label} {cause}; log: {log}", file=sys.stderr)
        return rc
    observed = a.result["observed_model"]
    if a.exact_model and observed and observed != a.model:
        err = f"CLI reported model {observed!r} instead of {a.model!r}"
    if not isinstance(answer, str):
        answer, err = "", "the CLI response is not text"
    elif not a.artifact_dir and not a.prompt_only:
        answer = answer.strip()
    if rc != 0 or err or not answer.strip():
        error = a.result.get("error") or err or ("empty response" if rc == 0 else "CLI failed")
        write_result(a, work, status="failed", error=error)
        # Keep a partial answer away from the success stream and final file.
        print(f"xllm: {label} failed (exit {rc}, {error}); log: {log}", file=sys.stderr)
        if answer:
            print("--- partial answer\n" + answer[: a.max_chars], file=sys.stderr)
        return rc or 1

    full = os.path.join(work, "xllm-output.txt" if a.artifact_dir else "answer_full.txt")
    truncated = len(answer) > a.max_chars
    if a.artifact_dir or truncated:
        with open(full, "w", encoding="utf-8", newline="") as f:
            f.write(answer)
        a.result["output_sha256"] = hashlib.sha256(answer.encode("utf-8")).hexdigest()
    write_result(a, work, status="completed")
    print(f"[model: {label}]")
    if truncated:
        print(answer[: a.max_chars] + f"\n\n[truncated; full answer: {full}]")
    else:
        print(answer, end="" if answer.endswith("\n") else "\n")
        if not a.artifact_dir:
            shutil.rmtree(work, ignore_errors=True)
    return 0


def main():
    p = argparse.ArgumentParser(description="Ask an external model through its CLI.")
    p.add_argument("--provider", choices=sorted(PROVIDERS), default="codex")
    p.add_argument("--model", help="tier (lightest, light, standard, strongest), a short family "
                   "name (astra, sol, argon, flash, opus...), or a full model id; "
                   "omit to use the CLI's own default")
    p.add_argument("--effort", choices=EFFORTS, help="reasoning effort")
    p.add_argument("--read-only", action="store_true",
                   help="take file writes away (default: Codex's default permissions "
                        "plus file writes)")
    p.add_argument("--prompt-only", action="store_true",
                   help="use only the prompt in an empty --cwd (Codex and Claude)")
    p.add_argument("--exact-model", action="store_true",
                   help="pass the required --model and --effort to the CLI as given")
    p.add_argument("--artifact-dir",
                   help="retain the response, raw logs and status in a new absolute directory")
    p.add_argument("--cancel-file",
                   help="stop the invocation when the given absolute path is created")
    p.add_argument("--cwd",
                   help="project the model works in and reads AGENTS.md from (default: current dir)")
    p.add_argument("--prompt-file", help="read the prompt from this UTF-8 file instead of stdin")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="seconds")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    p.add_argument("--list", metavar="PROVIDER", choices=sorted(PROVIDERS),
                   help="list the models and efforts a provider offers, then exit")
    a = p.parse_args()

    for s in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")

    try:
        if a.list:
            for mid, efforts in list_models(a.list):
                print(f"{mid}\t{','.join(efforts)}")
            print("# tiers:")
            for t in load_tiers():
                try:
                    mid, eff = resolve_model(a.list, t, None)
                    print(f"#   {t} -> {mid} ({eff})")
                except XllmError:
                    print(f"#   {t} -> unavailable on this account")
            return 0

        if a.prompt_file:
            with open(a.prompt_file, encoding="utf-8-sig") as f:
                prompt = f.read()
        elif sys.stdin is None or sys.stdin.isatty():
            raise XllmError("no prompt: pass --prompt-file or pipe the prompt on stdin")
        else:
            prompt = sys.stdin.read()
        prompt = prompt.lstrip("\ufeff").strip()
        if not prompt:
            raise XllmError("empty prompt")
        if a.timeout <= 0 or a.max_chars <= 0:
            raise XllmError("--timeout and --max-chars must be positive")
        if a.prompt_only and (a.provider == "agy" or not a.cwd):
            raise XllmError("--prompt-only requires Claude or Codex and an empty --cwd")
        a.cwd = os.path.abspath(a.cwd or os.getcwd())
        if not os.path.isdir(a.cwd):
            raise XllmError(f"--cwd is not a directory: {a.cwd}")
        if a.prompt_only and os.listdir(a.cwd):
            raise XllmError("--prompt-only requires an empty --cwd")
        if a.exact_model:
            if not a.model or not a.effort:
                raise XllmError("--exact-model requires --model and --effort")
        else:
            a.model, a.effort = resolve_model(a.provider, a.model, a.effort)
        for option, path in (("--artifact-dir", a.artifact_dir), ("--cancel-file", a.cancel_file)):
            if path and not os.path.isabs(path):
                raise XllmError(f"{option} requires an absolute path")
        if a.artifact_dir:
            work = os.path.abspath(a.artifact_dir)
            if a.prompt_only:
                cwd = os.path.normcase(os.path.realpath(a.cwd))
                target = os.path.normcase(os.path.realpath(work))
                if target == cwd or target.startswith(os.path.join(cwd, "")):
                    raise XllmError("--artifact-dir must be outside the empty --cwd")
            os.mkdir(work)
        else:
            work = tempfile.mkdtemp(prefix="xllm-")
    except (XllmError, OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
        # ValueError covers bad JSON in tiers.json and a prompt file that is not UTF-8.
        print(f"xllm: {e}", file=sys.stderr)
        return 2

    a.interrupted = False
    a.result = dict(status="running", exit_code=None, cleanup_requested=None, cleanup_verified=None,
                    provider=a.provider, model=a.model, observed_model=None, effort=a.effort,
                    command=None, usage=None, cwd=a.cwd, error=None, runner_pid=os.getpid(),
                    pid=None, pgid=None, output_sha256=None)

    def interrupt(_signal, _frame):
        a.interrupted = True

    handlers = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        write_result(a, work)
        return run_attempt(prompt, a, work)
    except OSError as e:
        print(f"xllm: cannot save run result: {e}", file=sys.stderr)
        return 3
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main())
