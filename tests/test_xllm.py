"""Offline tests for xllm.py (TESTPLAN.md sections 1 and 2). Run: python -m unittest -v tests/test_xllm.py"""

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "plugins", "external-llm", "skills", "external-llm", "scripts")
XLLM = os.path.abspath(os.path.join(SCRIPTS, "xllm.py"))
sys.path.insert(0, SCRIPTS)
import xllm  # noqa: E402

E5 = ["low", "medium", "high", "xhigh"]
FAKE = {
    "codex": [("gpt-6.1-sol", E5), ("gpt-6-astra", E5), ("gpt-6.1-terra", E5),
              ("gpt-6-sol", E5), ("gpt-6-luna", ["low", "medium"])],
    "agy": [("gemini-4-argon", ["low", "medium", "high"]), ("gemini-4-hydrogen", E5),
            ("gemini-3.8-flash-lite", []), ("gemini-3.8-flash", ["low", "medium", "high"])],
}


def fake_lists(lists):
    def list_models(provider):
        return xllm.CLAUDE_MODELS if provider == "claude" else lists.get(provider, [])
    return mock.patch.object(xllm, "list_models", list_models)


def run(args, stdin=None, env=None, cwd=None):
    e = dict(os.environ, **(env or {}))
    return subprocess.run([sys.executable, XLLM] + args, input=stdin, capture_output=True,
                          env=e, cwd=cwd, timeout=120)


class Resolution(unittest.TestCase):
    def test_1a_claude_tiers(self):
        got = [xllm.resolve_model("claude", t, None) for t in ("lightest", "light", "standard", "strongest")]
        self.assertEqual(got, [("haiku", "low"), ("sonnet", "low"), ("opus", "medium"), ("fable", "high")])

    def test_1b_codex_tiers_and_fallback(self):
        with fake_lists(FAKE):
            self.assertEqual(xllm.resolve_model("codex", "lightest", None), ("gpt-6-luna", "low"))
            self.assertEqual(xllm.resolve_model("codex", "light", None), ("gpt-6.1-terra", "low"))
            self.assertEqual(xllm.resolve_model("codex", "standard", None), ("gpt-6.1-sol", "medium"))
            self.assertEqual(xllm.resolve_model("codex", "strongest", None), ("gpt-6-astra", "high"))
        no_astra = {"codex": [m for m in FAKE["codex"] if "astra" not in m[0]]}
        with fake_lists(no_astra):
            self.assertEqual(xllm.resolve_model("codex", "strongest", None), ("gpt-6.1-sol", "high"))

    def test_1c_tier_without_model_list(self):
        with tempfile.TemporaryDirectory() as empty:
            r = run(["--provider", "codex", "--model", "lightest"], b"hi", {"CODEX_HOME": empty})
        self.assertEqual(r.returncode, 2)
        self.assertIn("cannot list codex models", r.stderr.decode())

    def test_1d_flash_prefers_exact_family(self):
        with fake_lists(FAKE):
            self.assertEqual(xllm.resolve_model("agy", "lightest", None)[0], "gemini-3.8-flash")
            self.assertEqual(xllm.resolve_model("agy", "flash", None)[0], "gemini-3.8-flash")

    def test_1e_agy_strongest_needs_carbon(self):
        with fake_lists(FAKE):
            with self.assertRaisesRegex(xllm.XllmError, "carbon"):
                xllm.resolve_model("agy", "strongest", None)
        carbon = {"agy": FAKE["agy"] + [("gemini-4-carbon", [])]}
        with fake_lists(carbon):
            self.assertEqual(xllm.resolve_model("agy", "strongest", None), ("gemini-4-carbon", None))

    def test_1f_unknown_names(self):
        with fake_lists(FAKE):
            with self.assertRaisesRegex(xllm.XllmError, "--list"):
                xllm.resolve_model("codex", "nosuch", None)
            self.assertEqual(xllm.resolve_model("codex", "gpt-9-new", "low"), ("gpt-9-new", "low"))

    def test_1g_effort(self):
        with fake_lists(FAKE):
            self.assertEqual(xllm.resolve_model("codex", "lightest", "medium"), ("gpt-6-luna", "medium"))
            self.assertEqual(xllm.resolve_model("codex", "luna", "xhigh"), ("gpt-6-luna", "medium"))
            self.assertEqual(xllm.resolve_model("agy", "flash-lite", "high"), ("gemini-3.8-flash-lite", None))

    def test_1h_no_substring_match(self):
        with fake_lists(FAKE):
            with self.assertRaises(xllm.XllmError):
                xllm.resolve_model("codex", "so", None)


class ErrorPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="xllm-test-")

    def write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def assert_clean_exit(self, r, code, text):
        err = r.stderr.decode("utf-8", "replace")
        self.assertEqual(r.returncode, code, err)
        self.assertIn(text, err)
        self.assertNotIn("Traceback", err)

    def test_2a_empty_prompt(self):
        r = run(["--provider", "claude", "--prompt-file", self.write("e.txt", b"  \n")])
        self.assert_clean_exit(r, 2, "empty prompt")

    def test_2b_cp949_prompt(self):
        r = run(["--provider", "claude", "--prompt-file", self.write("k.txt", "안녕".encode("cp949"))])
        self.assert_clean_exit(r, 2, "xllm:")

    def test_2c_bom_removed(self):
        path = self.write("b.txt", b"\xef\xbb\xbfhello")
        with mock.patch.object(sys, "argv", ["xllm", "--provider", "claude", "--prompt-file", path]), \
                mock.patch.dict(xllm.PROVIDERS, {"claude": lambda p, a, w: (0, repr(p), None)}):
            with mock.patch("builtins.print") as pr:
                self.assertEqual(xllm.main(), 0)
        self.assertEqual(pr.call_args_list[-1].args[0], "'hello'")

    def test_2d_bad_cwd(self):
        r = run(["--provider", "claude", "--cwd", os.path.join(self.tmp, "nope")], b"hi")
        self.assert_clean_exit(r, 2, "--cwd is not a directory")

    def test_2e_broken_tiers(self):
        bad = self.write("t.json", b"{ not json")
        r = run(["--provider", "claude", "--model", "lightest"], b"hi", {"XLLM_TIERS": bad})
        self.assert_clean_exit(r, 2, "xllm:")

    def test_2f_cli_missing_no_leftover(self):
        tmpdir = os.path.join(self.tmp, "t")
        os.mkdir(tmpdir)
        env = {"PATH": os.path.dirname(sys.executable), "TMP": tmpdir, "TEMP": tmpdir, "TMPDIR": tmpdir}
        r = run(["--provider", "codex"], b"hi", env)
        self.assert_clean_exit(r, 3, "not found")
        self.assertEqual(os.listdir(tmpdir), [])

    def test_2g_bad_arguments(self):
        self.assertEqual(run(["--provider", "foo"], b"hi").returncode, 2)
        self.assertEqual(run(["--effort", "ultra"], b"hi").returncode, 2)


FAKE_CLI = r'''
import json, os, subprocess, sys, time
mode = os.environ["FAKE_MODE"]
sys.stdin.read()
if mode == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    open(os.environ["FAKE_PID"], "w").write(str(child.pid))
    time.sleep(300)
elif mode == "partial":
    out = sys.argv[sys.argv.index("-o") + 1]
    open(out, "w", encoding="utf-8").write("half an answer")
    print("boom", file=sys.stderr)
    sys.exit(1)
elif mode.startswith("claude"):
    assert sys.argv[sys.argv.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in sys.argv
    assert "--safe-mode" in sys.argv
    model = sys.argv[sys.argv.index("--model") + 1]
    if mode == "claude_model":
        model = "claude-other"
    tools = ["Bash"] if mode == "claude_tools" else []
    print(json.dumps({"type": "system", "subtype": "init", "model": model,
                      "tools": tools, "mcp_servers": []}))
    print(json.dumps({"type": "result", "subtype": "success", "result": "  reply\n",
                      "is_error": False, "usage": {"input_tokens": 2}}))
else:
    if mode.startswith("background"):
        child = subprocess.Popen([sys.executable, "-c", "import sys, time; time.sleep(float(sys.argv[1]))",
                                  "0.6" if mode == "background_transient" else "300"])
        with open(os.environ["FAKE_PID"], "w") as f:
            f.write(str(child.pid))
    out = sys.argv[sys.argv.index("-o") + 1]
    answer = "  reply\n" if mode != "empty" else " "
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write(answer)
    print("diagnostic", file=sys.stderr)
    if "--json" in sys.argv:
        print(json.dumps({"type": "item.completed", "item": {
            "type": "agent_message", "text": "other" if mode == "mismatch" else answer}}))
        if mode == "tool":
            print(json.dumps({"type": "item.completed", "item": {"type": "mcp_tool_call"}}))
        if mode != "incomplete":
            print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}))
'''


def alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class FakeCli(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="xllm-fakebin-")
        self.addCleanup(tmp.cleanup)
        self.bin = tmp.name
        with open(os.path.join(self.bin, "fake_cli.py"), "w") as f:
            f.write(FAKE_CLI)
        for provider in ("codex", "claude"):
            if os.name == "nt":
                with open(os.path.join(self.bin, provider + ".cmd"), "w", newline="") as f:
                    f.write(f'@"{sys.executable}" "%~dp0fake_cli.py" %*\r\n')
            else:
                p = os.path.join(self.bin, provider)
                with open(p, "w") as f:
                    f.write(f'#!/bin/sh\nexec "{sys.executable}" "$(dirname "$0")/fake_cli.py" "$@"\n')
                os.chmod(p, 0o755)
        self.env = {"PATH": self.bin + os.pathsep + os.environ["PATH"]}

    def test_2h_timeout_kills_tree(self):
        pid_file = os.path.join(self.bin, "pid")
        start = time.time()
        r = run(["--provider", "codex", "--timeout", "1"], b"hi",
                dict(self.env, FAKE_MODE="hang", FAKE_PID=pid_file))
        took = time.time() - start
        err = r.stderr.decode()
        self.assertEqual(r.returncode, 124, err)
        self.assertLess(took, 60)
        log = err.rsplit("log: ", 1)[1].strip()
        self.assertTrue(os.path.exists(log), log)
        time.sleep(1)
        with open(pid_file) as f:
            self.assertFalse(alive(int(f.read())), "child still running")

    def test_2i_partial_answer_not_on_stdout(self):
        r = run(["--provider", "codex"], b"hi", dict(self.env, FAKE_MODE="partial"))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, b"")
        self.assertIn("half an answer", r.stderr.decode())

    def strict_args(self, answer_path, provider="codex"):
        context = Path(self.bin) / "empty context"
        context.mkdir(exist_ok=True)
        model = "gpt-test-specific" if provider == "codex" else "claude-test-specific"
        return ["--provider", provider, "--prompt-only", "--exact-model",
                "--model", model, "--effort", "xhigh", "--cwd", str(context),
                "--artifact-dir", str(answer_path)]

    def test_prompt_only_preserves_raw_answer_and_exact_args(self):
        work = Path(self.bin) / "new attempt"
        args = self.strict_args(work)
        attempted = run(args, b"synthetic prompt", dict(self.env, FAKE_MODE="ok"))
        self.assertEqual(attempted.returncode, 0, attempted.stderr)
        data = (work / "xllm-output.txt").read_bytes()
        self.assertEqual(data, b"  reply\n")
        self.assertEqual((work / "xllm-stderr.log").read_text(), "diagnostic\n")
        result = json.loads((work / "xllm-result.json").read_text())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["model"], "gpt-test-specific")
        self.assertEqual(result["usage"], {"input_tokens": 1})
        self.assertEqual(result["output_sha256"], hashlib.sha256(data).hexdigest())
        cmd = result["command"]
        self.assertEqual(cmd[cmd.index("-m") + 1], "gpt-test-specific")
        self.assertIn('model_reasoning_effort="xhigh"', cmd)
        self.assertIn("features.shell_tool=false", cmd)
        self.assertIn("--ignore-user-config", cmd)
        again = run(args, b"synthetic prompt", dict(self.env, FAKE_MODE="ok"))
        self.assertEqual(again.returncode, 2, again.stderr)
        self.assertEqual((work / "xllm-output.txt").read_bytes(), data)

    def test_prompt_only_rejects_incomplete_unmatched_tool_and_blank_answers(self):
        for mode in ("incomplete", "mismatch", "tool", "empty"):
            with self.subTest(mode=mode):
                work = Path(self.bin) / mode
                r = run(self.strict_args(work), b"synthetic prompt", dict(self.env, FAKE_MODE=mode))
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertEqual(r.stdout, b"")
                self.assertFalse((work / "xllm-output.txt").exists())
                result = json.loads((work / "xllm-result.json").read_text())
                self.assertEqual(result["status"], "failed")

    def test_prompt_only_checks_claude_tools_and_model(self):
        for mode, expected in (("claude", 0), ("claude_tools", 1), ("claude_model", 1)):
            with self.subTest(mode=mode):
                work = Path(self.bin) / ("attempt_" + mode)
                r = run(self.strict_args(work, "claude"), b"synthetic prompt",
                        dict(self.env, FAKE_MODE=mode))
                self.assertEqual(r.returncode, expected, r.stderr)
                result = json.loads((work / "xllm-result.json").read_text())
                self.assertEqual(result["status"], "failed" if expected else "completed")
                self.assertEqual((work / "xllm-output.txt").exists(), expected == 0)

    def test_prompt_only_rejects_unsupported_provider_and_nonempty_directory(self):
        work = Path(self.bin) / "rejected"
        r = run(self.strict_args(work, "agy"), b"synthetic prompt", self.env)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse(work.exists())
        (Path(self.bin) / "empty context" / "nonempty").touch()
        r = run(self.strict_args(work), b"synthetic prompt", self.env)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse(work.exists())

    def test_preexisting_cancel_request_never_launches(self):
        work = Path(self.bin) / "unstarted"
        stop = Path(self.bin) / "stop before start"
        stop.touch()
        r = run(self.strict_args(work) + ["--cancel-file", str(stop)], b"synthetic prompt",
                dict(self.env, FAKE_MODE="ok"))
        self.assertEqual(r.returncode, 130, r.stderr)
        result = json.loads((work / "xllm-result.json").read_text())
        self.assertEqual(result["status"], "cancelled")
        self.assertIsNone(result["pid"])
        self.assertTrue(result["cleanup_verified"])

    @unittest.skipIf(os.name == "nt", "POSIX process groups")
    def test_group_permission_error_is_not_proof_of_cleanup(self):
        proc = mock.Mock(pid=123, returncode=0)
        proc.poll.return_value = 0
        with mock.patch.object(xllm, "STOP_GRACE", 0), \
                mock.patch.object(os, "killpg", side_effect=PermissionError):
            self.assertTrue(xllm.posix_group_exists(proc.pid))
            self.assertEqual(xllm.kill_tree(proc), (False, False))
            proc.wait.assert_called_once()
        with mock.patch.object(os, "killpg",
                               side_effect=[PermissionError(), PermissionError(), ProcessLookupError()]):
            self.assertEqual(xllm.kill_tree(proc), (False, True))

    @unittest.skipIf(os.name == "nt", "POSIX process groups")
    def test_background_child_compatibility_and_prompt_only_cleanup(self):
        pid_file = Path(self.bin) / "background.pid"
        env = dict(self.env, FAKE_MODE="background", FAKE_PID=str(pid_file))
        normal = run(["--provider", "codex"], b"synthetic prompt", env)
        pid = int(pid_file.read_text())
        try:
            self.assertEqual(normal.returncode, 0, normal.stderr)
            self.assertEqual(normal.stdout, b"[model: codex]\nreply\n")
            self.assertTrue(alive(pid))
        finally:
            if alive(pid):
                os.kill(pid, signal.SIGKILL)

        transient = Path(self.bin) / "transient"
        short = run(self.strict_args(transient), b"synthetic prompt",
                    dict(env, FAKE_MODE="background_transient"))
        self.assertEqual(short.returncode, 0, short.stderr)
        transient_status = json.loads((transient / "xllm-result.json").read_text())
        self.assertTrue(transient_status["cleanup_verified"])

        residual = Path(self.bin) / "residual"
        blocked = run(self.strict_args(residual), b"synthetic prompt", env)
        pid = int(pid_file.read_text())
        try:
            self.assertEqual(blocked.returncode, 1, blocked.stderr)
            self.assertFalse(alive(pid))
            result = json.loads((residual / "xllm-result.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["exit_code"], 0)
            self.assertFalse((residual / "xllm-output.txt").exists())
        finally:
            if alive(pid):
                os.kill(pid, signal.SIGKILL)

    def test_cancel_kills_only_its_own_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            siblings = [(parent / suffix, parent / (suffix + ".stop"), parent / (suffix + ".pid"))
                        for suffix in ("first", "second")]
            procs = []
            try:
                for work, stop, pid_file in siblings:
                    env = dict(os.environ, **self.env, FAKE_MODE="hang", FAKE_PID=str(pid_file))
                    args = self.strict_args(work) + ["--cancel-file", str(stop)]
                    p = subprocess.Popen([sys.executable, XLLM] + args, stdin=subprocess.PIPE,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
                    procs.append(p)
                    p.stdin.write(b"synthetic prompt")
                    p.stdin.close()
                deadline = time.monotonic() + 15
                while not all(pid.exists() and pid.stat().st_size for _, _, pid in siblings):
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.05)
                work, stop, pid_file = siblings[0]
                child = int(pid_file.read_text())
                other_child = int(siblings[1][2].read_text())
                status = json.loads((work / "xllm-result.json").read_text())
                self.assertEqual(status["status"], "running")
                self.assertIsNotNone(status["pid"])
                stop.touch()
                self.assertEqual(procs[0].wait(timeout=15), 130, procs[0].stderr.read())
                self.assertFalse(alive(child), "cancelled descendant still running")
                self.assertIsNone(procs[1].poll())
                self.assertTrue(alive(other_child), "unrelated invocation was stopped")
                status = json.loads((work / "xllm-result.json").read_text())
                self.assertEqual(status["status"], "cancelled")
                self.assertFalse((work / "xllm-output.txt").exists())
            finally:
                for p, (_, stop, _) in zip(procs, siblings):
                    stop.touch()
                    p.wait(timeout=15)
                    p.stderr.close()


if __name__ == "__main__":
    unittest.main()
