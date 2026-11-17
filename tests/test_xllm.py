"""Offline tests for xllm.py (TESTPLAN.md sections 1 and 2). Run: python -m unittest -v tests/test_xllm.py"""

import json
import os
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
import os, subprocess, sys, time
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
        self.bin = tempfile.mkdtemp(prefix="xllm-fakebin-")
        with open(os.path.join(self.bin, "fake_cli.py"), "w") as f:
            f.write(FAKE_CLI)
        if os.name == "nt":
            with open(os.path.join(self.bin, "codex.cmd"), "w") as f:
                f.write(f'@"{sys.executable}" "%~dp0fake_cli.py" %*\r\n')
        else:
            p = os.path.join(self.bin, "codex")
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


if __name__ == "__main__":
    unittest.main()
