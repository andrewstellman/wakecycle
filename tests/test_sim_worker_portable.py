"""instr 037: the cross-platform work simulator (tests/integration/stub_worker.py)
must be STDLIB-ONLY and POSIX-call-free so it runs identically on
Windows/macOS/Linux (NFR-1/3). A mechanical AST guard, mirroring the
encoding-sweep discipline — it bites if someone adds a platform-specific call.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

_SIM = (Path(__file__).resolve().parents[1] / "tests" / "integration"
        / "stub_worker.py")

# modules that only exist (or only behave) on one platform
_BANNED_MODULES = {"fcntl", "pwd", "grp", "termios", "resource", "posix",
                   "syslog", "msvcrt", "winreg", "_winapi"}
# os.* calls that are POSIX-only (would NameError/AttributeError on Windows)
_BANNED_OS_CALLS = {"fork", "forkpty", "setsid", "getuid", "geteuid",
                    "getgid", "kill", "killpg", "wait", "waitpid"}


class SimulatorPortability(unittest.TestCase):

    def setUp(self):
        self.tree = ast.parse(_SIM.read_text(encoding="utf-8"))

    def test_no_posix_only_imports(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    self.assertNotIn(n.name.split(".")[0], _BANNED_MODULES,
                                     "platform-specific import: %s" % n.name)
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn((node.module or "").split(".")[0],
                                 _BANNED_MODULES,
                                 "platform-specific import: %s" % node.module)

    def test_no_posix_only_os_calls(self):
        for node in ast.walk(self.tree):
            if (isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "os"):
                self.assertNotIn(node.attr, _BANNED_OS_CALLS,
                                 "POSIX-only call os.%s" % node.attr)

    def test_every_text_open_pins_encoding(self):
        # default codec differs by platform; every text open() must pin one
        for node in ast.walk(self.tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "open"):
                kw = {k.arg for k in node.keywords}
                self.assertIn("encoding", kw,
                              "open() without encoding= at line %d" % node.lineno)


if __name__ == "__main__":
    unittest.main()
