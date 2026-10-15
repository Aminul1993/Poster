"""The ES-module checker in tools/check_js.py, and the dashboard's modules themselves."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from tests.conftest import ROOT

sys.path.insert(0, str(ROOT / "tools"))

from check_js import check, code_only


def test_dashboard_modules_are_consistent() -> None:
    assert check() == []


def test_lexer_skips_comments_strings_and_regexes() -> None:
    src = "const a = /[\"'`]+/g.test(x); // b(\n/* c() */ const d = `e ${f(g)} h`; const i = 'j(k)'; const l = m / n / o;"
    words = set(re.findall(r"[A-Za-z_$][\w$]*", code_only(src)))
    assert {"a", "x", "d", "f", "g", "i", "l", "m", "n", "o"} <= words
    assert not {"b", "c", "e", "h", "j", "k"} & words


def test_finds_problems(tmp_path: Path) -> None:
    (tmp_path / "a.js").write_text("export function helper() {}\nexport const value = 1;\n", encoding="utf-8")
    (tmp_path / "b.js").write_text("import { value, missing } from './a.js';\nimport { x } from './c.js';\nhelper();\n",
                                   encoding="utf-8")
    (tmp_path / "c.js").write_text("import { y } from './d.js';\nexport const x = y;\n", encoding="utf-8")
    (tmp_path / "d.js").write_text("import { x } from './c.js';\nexport const y = () => x;\n", encoding="utf-8")
    problems = check(tmp_path)
    assert "b.js: imports missing from ./a.js, which does not export it" in problems
    assert "b.js: uses helper (from a.js) without importing it" in problems
    assert "b.js: imports value but never uses it" in problems
    assert any(p.startswith("import cycle: c.js -> d.js -> c.js") for p in problems)
