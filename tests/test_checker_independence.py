"""FR-51 mechanically-enforced independence: the integration checker imports
the STANDARD LIBRARY ONLY -- never the ``wakecycle`` package, never a ``bin/``
module. "The harness never grades its own homework" must stay true as the
suite grows; this AST scan makes a violation fail loudly the moment it lands
(cheap now, effectively impossible to retrofit later).

MUTATION-VERIFY EVIDENCE (instr 018): adding ``import wakecycle`` (or
``from bin import tick``) to checker.py makes this test FAIL; removing it ->
OK. Demonstrated in outputs/018.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

_CHECKER = (Path(__file__).resolve().parents[1] / "tests" / "integration"
            / "checker.py")
_BANNED = {"wakecycle", "tick", "ticker", "heartbeat", "demo_worker", "bin"}


def _imported_top_modules(src_path):
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                mods.add("<relative-import>")     # relative import = local = banned
            elif node.module:
                mods.add(node.module.split(".")[0])
    return mods


class CheckerIndependenceTests(unittest.TestCase):

    def setUp(self):
        self.mods = _imported_top_modules(_CHECKER)

    def test_checker_imports_nothing_from_the_harness(self):
        leaked = self.mods & _BANNED
        self.assertEqual(leaked, set(),
                         "integration checker must not import the harness; "
                         "found: %s" % sorted(leaked))
        self.assertNotIn("<relative-import>", self.mods,
                         "checker must not use relative imports (would reach "
                         "into the test package)")

    def test_checker_imports_only_stdlib(self):
        # Python 3.10+: every imported top-level module must be in the stdlib.
        stdlib = getattr(sys, "stdlib_module_names", None)
        if stdlib is None:
            self.skipTest("sys.stdlib_module_names unavailable (<3.10)")
        non_stdlib = {m for m in self.mods
                      if m != "<relative-import>" and m not in stdlib}
        self.assertEqual(non_stdlib, set(),
                         "integration checker imports non-stdlib modules: %s"
                         % sorted(non_stdlib))


if __name__ == "__main__":
    unittest.main()
