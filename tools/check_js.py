"""Checks the dashboard's ES modules (static/js) without Node: every name exported by one module and used by
another must be imported, imports must exist and be used, and modules must not import each other in a cycle.

Usage: python tools/check_js.py        (exits 1 when it finds a problem)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path, PurePosixPath

JS_DIR = Path(__file__).resolve().parent.parent / "static" / "js"

EXPORT_RE = re.compile(r"^export (?:const|let|function|async function|class) +([A-Za-z_$][\w$]*)", re.M)
DECL_RE = re.compile(r"^(?:export )?(?:const|let|function|async function|class) +([A-Za-z_$][\w$]*)", re.M)
METHOD_RE = re.compile(r"^\s+(?:async |static |get |set )*#?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{", re.M)
IMPORT_RE = re.compile(r"^import \{([^}]*)\} from '([^']+)';", re.M)
STAR_IMPORT_RE = re.compile(r"^import \* as ([A-Za-z_$][\w$]*) from '([^']+)';", re.M)
# Identifiers, but not property names after "." or "?.".
IDENT_RE = re.compile(r"(?<![\w$.])(?<!\?\.)[A-Za-z_$][\w$]*")
# A "/" after these starts a regular expression literal rather than a division.
REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^") | {""}
REGEX_AFTER_WORDS = {"return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "else", "do", "yield", "await"}


def code_only(src: str) -> str:
    """The source with comments, string contents and regex literals blanked out; ${...} in templates is kept."""
    out: list[str] = []
    i, n = 0, len(src)
    template_depth: list[int] = []  # brace depth at which each open ${ started
    depth = 0
    prev = ""  # last significant character, to tell a regex literal from a division
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j == -1 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            out.append(" ")
        elif c in "'\"":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            i = j + 1
            out.append(" '' ")
            prev = "'"
        elif c == "`" or (c == "}" and template_depth and template_depth[-1] == depth):
            if c == "}":
                template_depth.pop()
            j = i + 1
            while j < n and src[j] != "`" and not src.startswith("${", j):
                j += 2 if src[j] == "\\" else 1
            if j < n and src.startswith("${", j):
                template_depth.append(depth)
                i = j + 2
            else:
                i = j + 1
            out.append(" ")
            prev = "`"
        elif c == "/" and (prev in REGEX_AFTER or (prev.isalnum() and _last_word(out) in REGEX_AFTER_WORDS)):
            j, in_class = i + 1, False
            while j < n and (src[j] != "/" or in_class):
                if src[j] == "\\":
                    j += 2
                    continue
                in_class = (in_class or src[j] == "[") and src[j] != "]"
                j += 1
            j += 1
            while j < n and src[j].isalpha():
                j += 1
            i = j
            out.append(" /r/ ")
            prev = "/"
        else:
            depth += {"{": 1, "}": -1}.get(c, 0)
            out.append(c)
            if not c.isspace():
                prev = c
            i += 1
    return "".join(out)


def _last_word(out: list[str]) -> str:
    match = re.search(r"([A-Za-z_$][\w$]*)\s*$", "".join(out[-30:]))
    return match.group(1) if match else ""


def resolve(module: str, target: str) -> str:
    parts: list[str] = []
    for part in (PurePosixPath(module).parent / target).parts:
        if part == "..":
            parts.pop()
        elif part != ".":
            parts.append(part)
    return "/".join(parts)


def check(js_dir: Path = JS_DIR) -> list[str]:
    files = {p.relative_to(js_dir).as_posix(): p.read_text(encoding="utf-8") for p in sorted(js_dir.rglob("*.js"))}
    exports = {name: set(EXPORT_RE.findall(text)) for name, text in files.items()}
    owners: dict[str, set[str]] = {}
    for module, names in exports.items():
        for name in names:
            owners.setdefault(name, set()).add(module)

    problems: list[str] = []
    graph: dict[str, set[str]] = {}
    for module, text in files.items():
        imported: set[str] = set()
        graph[module] = set()
        for names, target in IMPORT_RE.findall(text):
            source = resolve(module, target)
            graph[module].add(source)
            if source not in files:
                problems.append(f"{module}: imports {target}, which does not exist")
                continue
            for name in (n.strip() for n in names.split(",") if n.strip()):
                imported.add(name)
                if name not in exports[source]:
                    problems.append(f"{module}: imports {name} from {target}, which does not export it")
        for _, target in STAR_IMPORT_RE.findall(text):
            graph[module].add(resolve(module, target))
        body = STAR_IMPORT_RE.sub("", IMPORT_RE.sub("", text))
        used = set(IDENT_RE.findall(code_only(body)))
        local = set(DECL_RE.findall(text)) | set(METHOD_RE.findall(text))
        for name in sorted(used - local - imported):
            if name in owners and module not in owners[name]:
                problems.append(f"{module}: uses {name} (from {', '.join(sorted(owners[name]))}) without importing it")
        problems.extend(f"{module}: imports {name} but never uses it" for name in sorted(imported - used))
    problems.extend(f"import cycle: {' -> '.join(cycle)}" for cycle in find_cycles(graph))
    return problems


def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    cycles: list[list[str]] = []
    state: dict[str, int] = {}  # 1: on the current path, 2: done
    path: list[str] = []

    def visit(node: str) -> None:
        state[node] = 1
        path.append(node)
        for nxt in sorted(graph.get(node, ())):
            if state.get(nxt) == 1:
                cycles.append([*path[path.index(nxt):], nxt])
            elif nxt in graph and not state.get(nxt):
                visit(nxt)
        path.pop()
        state[node] = 2

    for node in sorted(graph):
        if not state.get(node):
            visit(node)
    return cycles


def main() -> int:
    problems = check()
    for problem in problems:
        print(problem)
    print(f"static/js: {len(list(JS_DIR.rglob('*.js')))} modules, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
