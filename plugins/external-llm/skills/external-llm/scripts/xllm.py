#!/usr/bin/env python3
"""Run one prompt on an external model through its CLI and print the final answer.

Providers: codex (OpenAI Codex CLI), agy (Google Antigravity CLI), claude (Claude Code).
Every provider runs read-only in the working directory (--cwd, default: the
current directory), so it reads that project's AGENTS.md like it would on its own.

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
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

DEFAULT_TIMEOUT = 900
DEFAULT_MAX_CHARS = 20000
EFFORTS = ("low", "medium", "high", "xhigh", "max")
EFFORT_SUFFIX = re.compile(r"-(%s)$" % "|".join(EFFORTS))


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
    cmd = [find_exe("codex"), "exec",
           "--sandbox", "read-only",          # may read the project, never write
           "--skip-git-repo-check", "--ephemeral", "--color", "never",
           "-o", out_file]                    # runs in a.cwd (see call)
    if a.model:
        cmd += ["-m", a.model]
    if a.effort:
        cmd += ["-c", f'model_reasoning_effort="{a.effort}"']
    cmd.append("-")                           # prompt on stdin
    rc, _ = call(cmd, prompt.encode("utf-8"), a, work)
    answer = read_text(out_file)
    return rc, answer, None


def run_agy(prompt, a, work):
    cmd = [find_exe("agy"),
           "--mode", "plan", "--sandbox",     # plan mode: read and answer, no edits
           "--input-format", "stream-json", "--output-format", "stream-json",
           f"--print-timeout={a.timeout}s"]
    if a.model:
        cmd += ["--model", a.model]
    if a.effort:
        cmd += ["--effort", a.effort]
    cmd.append("-p=")                         # prompt comes from stdin below
    msg = json.dumps({"event": "user", "message": {"content": prompt}}, ensure_ascii=False)
    rc, out = call(cmd, (msg + "\n").encode("utf-8"), a, work)
    result = last_event(out, lambda e: e.get("event") == "result")
    if not result:
        return rc or 1, "", "no result event in agy output"
    r = result.get("result", {})
    err = None if r.get("status") == "SUCCESS" else (r.get("error") or r.get("status"))
    return rc, (r.get("response") or "").strip(), err


def run_claude(prompt, a, work):
    # dontAsk + read-only tools: no edits. (Plan mode is avoided on purpose:
    # it switches the session to a different model than the one requested.)
    cmd = [find_exe("claude"), "-p",
           "--permission-mode", "dontAsk", "--allowedTools", "Read,Grep,Glob",
           "--no-session-persistence", "--output-format", "json"]
    if a.model:
        cmd += ["--model", a.model]
    if a.effort:
        cmd += ["--effort", a.effort]
    rc, out = call(cmd, prompt.encode("utf-8"), a, work)
    result = last_event(out, lambda e: e.get("type") == "result")
    if not result:
        return rc or 1, "", "no result in claude output"
    err = result.get("subtype") if result.get("is_error") else None
    return rc, (result.get("result") or "").strip(), err


PROVIDERS = {"codex": run_codex, "agy": run_agy, "claude": run_claude}


# ---------------------------------------------------------------- helpers

def kill_tree(proc):
    """Kill the CLI and its children. On Windows the CLI is often a .cmd shim
    around node, and killing the shim alone leaves node holding the pipes."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            proc.kill()


def call(cmd, stdin_bytes, a, work):
    """Run the CLI in the project directory; keep raw output in cli.log."""
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)               # allow nesting Claude Code
    proc = subprocess.Popen(cmd, cwd=a.cwd, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=(os.name != "nt"))
    try:
        out, err = proc.communicate(stdin_bytes, timeout=a.timeout + 30)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out, err = b"", b"(no output: the CLI did not exit after being killed)"
        write_log(work, cmd, out or b"", (err or b"") + b"\n--- timed out")
        raise
    write_log(work, cmd, out, err)
    return proc.returncode, out.decode("utf-8", "replace")


def write_log(work, cmd, out, err):
    with open(os.path.join(work, "cli.log"), "w", encoding="utf-8") as log:
        log.write("$ " + " ".join(cmd) + "\n" + out.decode("utf-8", "replace")
                  + "\n--- stderr\n" + err.decode("utf-8", "replace"))


def last_event(text, pred):
    found = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if pred(e):
            found = e
    return found


def read_text(path):
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().strip()


def main():
    p = argparse.ArgumentParser(description="Ask an external model through its CLI.")
    p.add_argument("--provider", choices=sorted(PROVIDERS), default="codex")
    p.add_argument("--model", help="tier (lightest, light, standard, strongest), a short family "
                   "name (astra, sol, argon, flash, opus...), or a full model id; "
                   "omit to use the CLI's own default")
    p.add_argument("--effort", choices=EFFORTS, help="reasoning effort")
    p.add_argument("--cwd", default=os.getcwd(),
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
        a.cwd = os.path.abspath(a.cwd)
        if not os.path.isdir(a.cwd):
            raise XllmError(f"--cwd is not a directory: {a.cwd}")
        a.model, a.effort = resolve_model(a.provider, a.model, a.effort)
    except (XllmError, OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
        # ValueError covers bad JSON in tiers.json and a prompt file that is not UTF-8.
        print(f"xllm: {e}", file=sys.stderr)
        return 2

    work = tempfile.mkdtemp(prefix="xllm-")
    label = a.provider + (f"/{a.model}" if a.model else "") + (f"/{a.effort}" if a.effort else "")
    try:
        rc, answer, err = PROVIDERS[a.provider](prompt, a, work)
    except subprocess.TimeoutExpired:
        print(f"xllm: {label} timed out after {a.timeout}s; "
              f"log: {os.path.join(work, 'cli.log')}", file=sys.stderr)
        return 124
    except (XllmError, OSError) as e:
        shutil.rmtree(work, ignore_errors=True)
        print(f"xllm: {e}", file=sys.stderr)
        return 3

    if rc != 0 or err or not answer:
        # A partial answer goes to stderr so it cannot pass for a normal one.
        print(f"xllm: {label} failed (exit {rc}{', ' + str(err) if err else ''}); "
              f"log: {os.path.join(work, 'cli.log')}", file=sys.stderr)
        if answer:
            print("--- partial answer\n" + answer[: a.max_chars], file=sys.stderr)
        return rc or 1

    print(f"[model: {label}]")
    if len(answer) > a.max_chars:
        full = os.path.join(work, "answer_full.txt")
        with open(full, "w", encoding="utf-8") as f:
            f.write(answer)
        print(answer[: a.max_chars] + f"\n\n[truncated; full answer: {full}]")
    else:
        print(answer)
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
