"""A knowledge graph of this repo: what exists, and what touches what.

    uv run tools/kg.py build         # scan the repo into tools/kg.sqlite
    uv run tools/kg.py stats         # what is in there
    uv run tools/kg.py trace /admin/reject      # a route, end to end
    uv run tools/kg.py node compute_results     # one thing, both directions
    uv run tools/kg.py impact db.set_active_question   # what breaks if I change it
    uv run tools/kg.py check         # seams that have come apart
    uv run tools/kg.py map           # compact overview, for reading
    uv run tools/kg.py sql "SELECT ..."         # anything else

Why a graph rather than grep. The awkward questions here cross languages: a
route renders a template, the template emits a CSS class, app.js re-renders
that same markup on a poll, the CSS styles it, and the route wrote a row that
some other route reads. Grep answers one hop. Every bug worth the name in this
codebase has been two or three hops -- markup that only one of the two
renderers knew about, a selector left behind when its template went away.

Extraction is exact for Python (the ast module) and honest guesswork for the
rest (regex over templates, JS and CSS). Guesswork is fine for navigation and
for the checks below, which point you at a file rather than deciding anything.
Anything acted on automatically would need a real parser.

Nodes carry a kind and a name; edges carry a kind and the line that made them.
That is the whole model -- the queries are ordinary SQL.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(__file__).resolve().parent / "kg.sqlite"
PKG = ROOT / "src" / "lykkepoller"

SCHEMA = """
DROP TABLE IF EXISTS nodes;
DROP TABLE IF EXISTS edges;

-- One row per thing that exists: a module, a function, a route, a table, a
-- CSS class. `name` is what you would call it out loud; `id` is "kind:name"
-- so edges can point at it without ambiguity.
CREATE TABLE nodes (
    id    TEXT PRIMARY KEY,
    kind  TEXT NOT NULL,
    name  TEXT NOT NULL,
    file  TEXT,
    line  INTEGER,
    meta  TEXT
);

-- One row per relationship, with the line that created it, so an answer can
-- always be turned back into a place to look.
CREATE TABLE edges (
    src   TEXT NOT NULL,
    dst   TEXT NOT NULL,
    kind  TEXT NOT NULL,
    file  TEXT,
    line  INTEGER
);

CREATE INDEX idx_nodes_kind ON nodes(kind);
CREATE INDEX idx_nodes_name ON nodes(name);
CREATE INDEX idx_edges_src  ON edges(src, kind);
CREATE INDEX idx_edges_dst  ON edges(dst, kind);
"""

# Jinja hands these to every template, and loop variables come from the loop,
# so neither is a missing context key.
TEMPLATE_BUILTINS = {"loop", "request", "url_for", "asset_version", "range", "dict"}

# Words that look like table names in SQL but are not.
SQL_NOISE = {"select", "where", "set", "values", "from", "into", "table", "if", "not", "exists"}


class Graph:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.edges: list[tuple] = []

    def node(self, kind, name, file=None, line=None, **meta) -> str:
        nid = f"{kind}:{name}"
        old = self.nodes.get(nid)
        if old is None:
            self.nodes[nid] = {"id": nid, "kind": kind, "name": name, "file": file,
                               "line": line, "meta": meta}
        elif old["file"] is None and file is not None:
            old.update(file=file, line=line)
            old["meta"].update(meta)
        return nid

    def edge(self, src, dst, kind, file=None, line=None):
        self.edges.append((src, dst, kind, file, line))

    def save(self, path: Path):
        conn = sqlite3.connect(path)
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO nodes (id, kind, name, file, line, meta) VALUES (?,?,?,?,?,?)",
            [(n["id"], n["kind"], n["name"], n["file"], n["line"], json.dumps(n["meta"]))
             for n in self.nodes.values()],
        )
        # Two files can make the same edge; the graph only cares that it exists.
        conn.executemany(
            "INSERT INTO edges (src, dst, kind, file, line) VALUES (?,?,?,?,?)",
            sorted(set(self.edges)),
        )
        conn.commit()
        conn.close()


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


# --- Python -------------------------------------------------------------------


def scan_python(g: Graph) -> dict[str, list[str]]:
    """Modules, classes, functions, routes, imports, calls and SQL.

    Returns name -> node id for every function, so the second pass can resolve
    calls once everything that exists is known.
    """
    defined: dict[str, list[str]] = {}
    files = sorted(PKG.glob("*.py")) + sorted((ROOT / "tests").glob("*.py"))

    for path in files:
        tree = ast.parse(path.read_text(), filename=str(path))
        mod = path.stem if path.parent == PKG else f"tests.{path.stem}"
        mid = g.node("module", mod, rel(path), 1)

        for n in ast.walk(tree):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for target in _import_targets(n):
                    g.edge(mid, g.node("module", target), "imports", rel(path), n.lineno)

            elif isinstance(n, ast.ClassDef):
                cid = g.node("class", f"{mod}.{n.name}", rel(path), n.lineno)
                g.edge(mid, cid, "defines", rel(path), n.lineno)

            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fid = g.node("function", f"{mod}.{n.name}", rel(path), n.lineno,
                             is_test=n.name.startswith("test_"))
                g.edge(mid, fid, "defines", rel(path), n.lineno)
                defined.setdefault(n.name, []).append(fid)
                for route in _routes_of(n):
                    rid = g.node("route", route, rel(path), n.lineno)
                    g.edge(rid, fid, "handled_by", rel(path), n.lineno)
                    g.edge(mid, rid, "serves", rel(path), n.lineno)
                for table, op in _sql_of(n):
                    g.edge(fid, g.node("table", table), op, rel(path), n.lineno)
                for tmpl in _templates_of(n):
                    g.edge(fid, g.node("template", tmpl), "renders", rel(path), n.lineno)
                for key in _context_keys_of(n):
                    g.edge(fid, g.node("context_key", key), "provides", rel(path), n.lineno)

        for table in _created_tables(path.read_text()):
            g.edge(mid, g.node("table", table), "declares", rel(path), 1)

    return defined


def _import_targets(node) -> list[str]:
    if isinstance(node, ast.Import):
        return [a.name.split(".")[0] for a in node.names]
    if node.level and node.module is None:      # from . import x
        return [a.name for a in node.names]
    return [node.module.split(".")[-1]] if node.module else []


def _routes_of(fn) -> list[str]:
    """FastAPI decorators: @app.get("/x") -> "GET /x"."""
    out = []
    for d in fn.decorator_list:
        if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and d.func.attr in ("get", "post", "put", "delete")
                and d.args and isinstance(d.args[0], ast.Constant)):
            out.append(f"{d.func.attr.upper()} {_norm_path(d.args[0].value)}")
    return out


def _strings_in(fn) -> list[str]:
    return [n.value for n in ast.walk(fn)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


SQL_OPS = [
    (r"\bfrom\s+([a-z_][a-z0-9_]*)", "reads"),
    (r"\bjoin\s+([a-z_][a-z0-9_]*)", "reads"),
    (r"\binto\s+([a-z_][a-z0-9_]*)", "writes"),
    (r"\bupdate\s+([a-z_][a-z0-9_]*)", "writes"),
    (r"\bdelete\s+from\s+([a-z_][a-z0-9_]*)", "writes"),
]


def _sql_of(fn) -> set[tuple[str, str]]:
    """Tables touched by SQL string literals inside a function.

    Every query in this codebase is a literal in db.py, which is what makes
    this reliable enough to trust. An f-string built query would slip past.
    """
    found = set()
    for s in _strings_in(fn):
        low = s.lower()
        if not any(k in low for k in ("select", "insert", "update", "delete")):
            continue
        for pattern, op in SQL_OPS:
            for m in re.finditer(pattern, low):
                if m.group(1) not in SQL_NOISE:
                    found.add((m.group(1), op))
    return found


def _created_tables(src: str) -> set[str]:
    # The paren matters: db.py's own comments say "CREATE TABLE IF NOT EXISTS
    # does not retro-add columns", and `does` is not a table.
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\(", src))


def _templates_of(fn) -> set[str]:
    return {s for s in _strings_in(fn) if s.endswith(".html")}


def _context_keys_of(fn) -> set[str]:
    """Keys of the dict handed to TemplateResponse -- what a template may use."""
    keys = set()
    for n in ast.walk(fn):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "TemplateResponse"):
            for arg in n.args:
                if isinstance(arg, ast.Dict):
                    keys |= {k.value for k in arg.keys
                             if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return keys


def resolve_calls(g: Graph, defined: dict[str, list[str]]):
    """Second pass: link call sites now that every definition is known.

    A bare name that two modules both define is skipped rather than guessed
    at -- a wrong edge is worse than a missing one, because you would act on it.
    """
    for path in sorted(PKG.glob("*.py")) + sorted((ROOT / "tests").glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        mod = path.stem if path.parent == PKG else f"tests.{path.stem}"
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            caller = f"function:{mod}.{fn.name}"
            for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
                name = (call.func.attr if isinstance(call.func, ast.Attribute)
                        else call.func.id if isinstance(call.func, ast.Name) else None)
                if not name:
                    continue
                targets = defined.get(name, [])
                same = [t for t in targets if t.startswith(f"function:{mod}.")]
                pick = same[0] if same else (targets[0] if len(targets) == 1 else None)
                if pick and pick != caller:
                    g.edge(caller, pick, "calls", rel(path), call.lineno)


# --- templates, JS, CSS -------------------------------------------------------

CLASS_RE = re.compile(r'class="([^"]*)"')
ACTION_RE = re.compile(r'action="(/[^"]*)"')
ASSET_RE = re.compile(r'(?:href|src)="/static/([^"?]+)')
BODY_CLASS_RE = re.compile(r"\{%\s*block body_class\s*%\}(\w+)")
VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_]\w*)|\{%\s*(?:if|elif)\s+(?:not\s+)?([a-zA-Z_]\w*)"
                    r"|\{%\s*for\s+[\w, ]+?\s+in\s+([a-zA-Z_]\w*)")
# Names a template makes for itself, which no handler has to provide.
SET_RE = re.compile(r"\{%\s*set\s+([\w, ]+?)\s*=")
CLASS_SET_RE = re.compile(r"\{%\s*set\s+(\w*(?:cls|class)\w*)\s*=(.*?)%\}", re.S | re.I)
FOR_TARGET_RE = re.compile(r"\{%\s*for\s+([\w, ]+?)\s+in\b")
JINJA_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
COMPARISON_RE = re.compile(r"(?:==|!=|\bin\b)\s*(?:'[^']*'|\"[^\"]*\")")
INLINE_VAR_RE = re.compile(r'style="[^"]*?(--[\w-]+)\s*:')
TOKEN_RE = re.compile(r"[a-zA-Z][\w-]*$")


def _class_tokens(value: str) -> list[str]:
    """Class names out of one class attribute, which may be half Jinja.

    Two sources, and both matter:
      class="bar{% if x %} correct{% endif %}"   -> literal text
      class="badge {{ 'on' if x else 'off' }}"   -> quoted words inside the
                                                    expression, which are
                                                    class names precisely
                                                    because of where they sit
    """
    out = [t for t in JINJA_RE.sub(" ", value).split() if TOKEN_RE.match(t)]
    for expr in JINJA_RE.findall(value):
        # A quoted word right after ==, != or `in` is being compared against,
        # not printed: {% if res.type == 'multiple_choice' %} inside a class
        # attribute is a condition, not a class.
        out += _quoted_words(COMPARISON_RE.sub(" ", expr))
    return out


def _quoted_words(text: str) -> list[str]:
    return [t for a, b in re.findall(r"'([^']*)'|\"([^\"]*)\"", text)
            for t in (a or b).split() if TOKEN_RE.match(t)]


def _quoted_class_tokens(src: str) -> set[str]:
    """Class names assembled in a variable, then used as class="{{ that }}".

    Only variables whose own name says they hold classes -- bar_cls, cls,
    row_class. Reading every {% set %} would drag in prose from things like
    {% set label = 'Showing on /present' %}.
    """
    out: set[str] = set()
    for m in CLASS_SET_RE.finditer(src):
        out |= set(_quoted_words(m.group(2)))
    return out


def _norm_path(p: str) -> str:
    """Make a URL comparable across a route decorator, a form action and a
    fetch: /answer/{session_id}, /answer/{{ session_id }} and
    "/api/state/" + id all collapse to the same shape."""
    p = re.sub(r"\{\{.*?\}\}|\{[^}]*\}", "{}", p).strip()
    return p if not p.endswith("/") or p == "/" else p + "{}"


def scan_templates(g: Graph):
    for path in sorted((PKG / "templates").glob("*.html")):
        src = path.read_text()
        tid = g.node("template", path.name, rel(path), 1)
        declared = set()
        for m in list(SET_RE.finditer(src)) + list(FOR_TARGET_RE.finditer(src)):
            declared |= {x.strip() for x in m.group(1).split(",")}

        for m in re.finditer(r'\{%\s*extends\s+"([^"]+)"', src):
            g.edge(tid, g.node("template", m.group(1)), "extends", rel(path), _line(src, m))

        for m in CLASS_RE.finditer(src):
            for cls in _class_tokens(m.group(1)):
                g.edge(tid, g.node("css_class", cls), "emits", rel(path), _line(src, m))
        for m in BODY_CLASS_RE.finditer(src):
            g.edge(tid, g.node("css_class", m.group(1)), "emits", rel(path), _line(src, m))
        for cls in _quoted_class_tokens(src):
            g.edge(tid, g.node("css_class", cls), "emits", rel(path), 1)

        for m in ACTION_RE.finditer(src):
            g.edge(tid, g.node("route", f"POST {_norm_path(m.group(1))}"),
                   "posts_to", rel(path), _line(src, m))

        for m in ASSET_RE.finditer(src):
            g.edge(tid, g.node("asset", m.group(1)), "uses", rel(path), _line(src, m))

        for m in INLINE_VAR_RE.finditer(src):
            g.edge(tid, g.node("css_var", m.group(1)), "defines", rel(path), _line(src, m))

        for m in VAR_RE.finditer(src):
            name = m.group(1) or m.group(2) or m.group(3)
            if name and name not in TEMPLATE_BUILTINS and name not in declared:
                g.edge(tid, g.node("context_key", name), "needs", rel(path), _line(src, m))


JS_FN_RE = re.compile(r"^(?:async\s+)?function\s+(\w+)", re.M)
JS_FETCH_RE = re.compile(r'fetchJson\(\s*"([^"]+)"|fetch\(\s*[a-zA-Z_.]+\.?action')
JS_ENDPOINT_RE = re.compile(r'"(/api/[^"]*)"')
JS_ACTION_RE = re.compile(r'action=\\?"(/[^"\\]*)')
JS_SELECTOR_RE = re.compile(r'querySelector(?:All)?\(\s*[`"\']([^`"\']+)')
JS_CLASS_ATTR_RE = re.compile(r'class=\\?"([^"\\]*)')
JS_INTERP_RE = re.compile(r"\$\{.*?\}", re.S)


def _js_class_tokens(value: str) -> list[str]:
    """Literal class names out of `class="bar ${cls}"`. The ${...} half is
    picked up by JS_CLASS_VAR_RE instead."""
    return [t for t in JS_INTERP_RE.sub(" ", value).split() if TOKEN_RE.match(t)]


JS_CLASS_VAR_RE = re.compile(r'\b\w*(?:[cC]ls|[cC]lassName)\w*\s*(?:=|\+=)[^;\n]*', re.M)
JS_INLINE_VAR_RE = re.compile(r'style="[^"]*?(--[\w-]+)\s*:')
JS_CLASSLIST_RE = re.compile(r'classList\.(?:toggle|add|remove)\(\s*"([\w-]+)"')


def scan_js(g: Graph):
    path = PKG / "static" / "app.js"
    src = path.read_text()
    aid = g.node("asset", "app.js", rel(path), 1)

    fns = {m.group(1): _line(src, m) for m in JS_FN_RE.finditer(src)}
    for name, line in fns.items():
        fid = g.node("js_function", name, rel(path), line)
        g.edge(aid, fid, "defines", rel(path), line)

    bodies = _js_bodies(src, fns)
    for name, body in bodies.items():
        fid = f"js_function:{name}"
        for other in fns:
            if other != name and re.search(rf"\b{other}\s*\(", body):
                g.edge(fid, f"js_function:{other}", "calls", rel(path), fns[name])
        for m in JS_ENDPOINT_RE.finditer(body):
            g.edge(fid, g.node("route", f"GET {_norm_path(m.group(1))}"),
                   "polls", rel(path), fns[name])
        for m in JS_ACTION_RE.finditer(body):
            g.edge(fid, g.node("route", f"POST {_norm_path(m.group(1))}"),
                   "posts_to", rel(path), fns[name])
        # Classes app.js writes into the DOM, and classes it reads back out.
        for m in JS_CLASS_ATTR_RE.finditer(body):
            for cls in _js_class_tokens(m.group(1)):
                g.edge(fid, g.node("css_class", cls), "emits", rel(path), fns[name])
        # `cls = a.approved ? "approved" : ""` -- the class is in a variable,
        # so the attribute above only sees ${cls}.
        for m in JS_CLASS_VAR_RE.finditer(body):
            for lit in re.findall(r'"([^"]*)"', m.group(0)):
                for tok in lit.split():
                    if TOKEN_RE.match(tok):
                        g.edge(fid, g.node("css_class", tok), "emits", rel(path), fns[name])
        for m in JS_INLINE_VAR_RE.finditer(body):
            g.edge(fid, g.node("css_var", m.group(1)), "defines", rel(path), fns[name])
        for m in JS_CLASSLIST_RE.finditer(body):
            g.edge(fid, g.node("css_class", m.group(1)), "emits", rel(path), fns[name])
        for m in JS_SELECTOR_RE.finditer(body):
            for cls in re.findall(r"\.([a-zA-Z][\w-]*)", m.group(1)):
                g.edge(fid, g.node("css_class", cls), "queries", rel(path), fns[name])


def _js_bodies(src: str, fns: dict[str, int]) -> dict[str, str]:
    """Split the file at each `function` line. Crude, but the file is flat and
    every function is top-level, so the slices are right."""
    lines = src.splitlines()
    starts = sorted((line - 1, name) for name, line in fns.items())
    out = {}
    for i, (start, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(lines)
        out[name] = "\n".join(lines[start:end])
    return out


CSS_RULE_RE = re.compile(r"^([^@{}/][^{}]*)\{", re.M)
CSS_VAR_DEF_RE = re.compile(r"(--[\w-]+)\s*:")
CSS_VAR_USE_RE = re.compile(r"var\(\s*(--[\w-]+)")


CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)


def scan_css(g: Graph):
    for path in sorted((PKG / "static").rglob("*.css")):
        # Comments in this project contain full sentences and filenames, and
        # "style.css" reads as a class called `css` to a regex.
        src = CSS_COMMENT_RE.sub("", path.read_text())
        sid = g.node("asset", str(path.relative_to(PKG / "static")), rel(path), 1)
        for m in CSS_RULE_RE.finditer(src):
            line = _line(src, m)
            for cls in set(re.findall(r"\.([a-zA-Z][\w-]*)", m.group(1))):
                g.edge(sid, g.node("css_class", cls), "styles", rel(path), line)
        for m in CSS_VAR_DEF_RE.finditer(src):
            g.edge(sid, g.node("css_var", m.group(1)), "defines", rel(path), _line(src, m))
        for m in CSS_VAR_USE_RE.finditer(src):
            g.edge(sid, g.node("css_var", m.group(1)), "uses", rel(path), _line(src, m))


def _line(src: str, m) -> int:
    return src.count("\n", 0, m.start()) + 1


# --- build --------------------------------------------------------------------


def sources():
    """Every file the scanners read. Also what staleness is measured against,
    so the two can never disagree about what the graph covers."""
    yield from PKG.glob("*.py")
    yield from (ROOT / "tests").glob("*.py")
    yield from (PKG / "templates").glob("*.html")
    yield from (PKG / "static").rglob("*.css")
    yield PKG / "static" / "app.js"


def is_stale() -> bool:
    if not DB_PATH.exists():
        return True
    built = DB_PATH.stat().st_mtime
    return any(p.stat().st_mtime > built for p in sources() if p.exists())


def build() -> Graph:
    g = Graph()
    defined = scan_python(g)
    resolve_calls(g, defined)
    scan_templates(g)
    scan_js(g)
    scan_css(g)
    g.save(DB_PATH)
    return g


# --- queries ------------------------------------------------------------------


def connect() -> sqlite3.Connection:
    # Rebuild rather than answer from a stale graph. A graph that is merely
    # out of date is worse than no graph: it answers confidently and wrongly,
    # and nothing about the answer says how old it is. The scan is well under
    # a second, so there is nothing to weigh up. Said on stderr so piping the
    # output of `sql` still works.
    if is_stale():
        print("(sources changed — rebuilding the graph)", file=sys.stderr)
        build()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def find(conn, term: str) -> list[sqlite3.Row]:
    """Match a node by exact id, exact name, then tail ("db.foo" or "foo")."""
    for sql, arg in (
        ("SELECT * FROM nodes WHERE id = ?", term),
        ("SELECT * FROM nodes WHERE name = ?", term),
        ("SELECT * FROM nodes WHERE name LIKE ?", f"%.{term}"),
        ("SELECT * FROM nodes WHERE name LIKE ?", f"%{term}%"),
    ):
        rows = conn.execute(sql, (arg,)).fetchall()
        if rows:
            return rows
    return []


def where(n) -> str:
    return f"  ({n['file']}:{n['line']})" if n["file"] else ""


def cmd_stats(conn, _args):
    print("nodes")
    for r in conn.execute("SELECT kind, COUNT(*) c FROM nodes GROUP BY kind ORDER BY c DESC"):
        print(f"  {r['c']:>5}  {r['kind']}")
    print("\nedges")
    for r in conn.execute("SELECT kind, COUNT(*) c FROM edges GROUP BY kind ORDER BY c DESC"):
        print(f"  {r['c']:>5}  {r['kind']}")


def cmd_node(conn, args):
    rows = find(conn, args.term)
    if not rows:
        sys.exit(f"nothing matches {args.term!r}")
    for n in rows[:8]:
        print(f"\n{n['kind']}  {n['name']}{where(n)}")
        out = conn.execute(
            "SELECT e.kind, e.line, n.kind dk, n.name dn FROM edges e "
            "JOIN nodes n ON n.id = e.dst WHERE e.src = ? ORDER BY e.kind, n.name", (n["id"],)
        ).fetchall()
        for r in out:
            print(f"    -> {r['kind']:<12} {r['dk']}:{r['dn']}")
        inc = conn.execute(
            "SELECT e.kind, n.kind sk, n.name sn FROM edges e "
            "JOIN nodes n ON n.id = e.src WHERE e.dst = ? ORDER BY e.kind, n.name", (n["id"],)
        ).fetchall()
        for r in inc:
            print(f"    <- {r['kind']:<12} {r['sk']}:{r['sn']}")


def cmd_impact(conn, args):
    """Everything that reaches this node, followed backwards to the source."""
    rows = find(conn, args.term)
    if not rows:
        sys.exit(f"nothing matches {args.term!r}")
    start = rows[0]
    print(f"changing {start['kind']}:{start['name']}{where(start)} is felt by:\n")
    seen, frontier, depth = {start["id"]}, [start["id"]], 0
    while frontier and depth < args.depth:
        depth += 1
        nxt = []
        for r in conn.execute(
            "SELECT DISTINCT n.id, n.kind, n.name, n.file, n.line, e.kind ek FROM edges e "
            f"JOIN nodes n ON n.id = e.src WHERE e.dst IN ({','.join('?' * len(frontier))})",
            frontier,
        ).fetchall():
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            nxt.append(r["id"])
            print(f"  {'  ' * depth}{r['ek']:<12} {r['kind']}:{r['name']}{where(r)}")
        frontier = nxt


def cmd_trace(conn, args):
    """A route, end to end: handler, tables, template, markup, script."""
    rows = [r for r in find(conn, args.term) if r["kind"] == "route"] or find(conn, args.term)
    if not rows:
        sys.exit(f"no route matches {args.term!r}")
    for route in rows[:4]:
        print(f"\n{route['name']}{where(route)}")
        for h in conn.execute(
            "SELECT n.id, n.name, n.file, n.line FROM edges e JOIN nodes n ON n.id = e.dst "
            "WHERE e.src = ? AND e.kind = 'handled_by'", (route["id"],)
        ):
            print(f"  handler   {h['name']}  ({h['file']}:{h['line']})")
            for r in conn.execute(
                "SELECT e.kind, n.kind dk, n.name dn FROM edges e JOIN nodes n ON n.id = e.dst "
                "WHERE e.src = ? AND e.kind IN ('reads','writes','renders','calls') "
                "ORDER BY e.kind, n.name", (h["id"],)
            ):
                print(f"    {r['kind']:<9} {r['dk']}:{r['dn']}")
        for s in conn.execute(
            "SELECT n.kind, n.name FROM edges e JOIN nodes n ON n.id = e.src "
            "WHERE e.dst = ? AND e.kind IN ('posts_to','polls') ORDER BY n.name", (route["id"],)
        ):
            print(f"  called by {s['kind']}:{s['name']}")


def cmd_check(conn, _args):
    """Seams that have come apart. Every finding names a file to look at."""
    checks = [
        ("markup drawn by only one of the two renderers",
         "A template and app.js both draw the admin and present pages. A class "
         "only one of them knows about means the poll either adds or wipes it.",
         """
         SELECT n.name,
                GROUP_CONCAT(DISTINCT CASE WHEN e.file LIKE '%templates%'
                                           THEN 'template' ELSE 'app.js' END) sides
         FROM edges e JOIN nodes n ON n.id = e.dst
         WHERE e.kind = 'emits' AND n.kind = 'css_class'
           AND n.name IN (SELECT n2.name FROM edges e2 JOIN nodes n2 ON n2.id = e2.dst
                          WHERE e2.kind = 'emits' AND e2.file LIKE '%app.js%')
         GROUP BY n.name HAVING sides = 'app.js'
         """),
        ("classes app.js looks for that nothing draws",
         "Left behind when the markup that had them was removed. The selector "
         "silently matches nothing.",
         """
         SELECT DISTINCT n.name FROM edges e JOIN nodes n ON n.id = e.dst
         WHERE e.kind = 'queries'
           AND n.id NOT IN (SELECT dst FROM edges WHERE kind = 'emits')
         """),
        ("styled but never drawn",
         "Dead CSS. Harmless, but it is the residue of a removed feature.",
         """
         SELECT DISTINCT n.name FROM edges e JOIN nodes n ON n.id = e.dst
         WHERE e.kind = 'styles'
           AND n.id NOT IN (SELECT dst FROM edges WHERE kind IN ('emits','queries'))
         """),
        ("drawn but never styled",
         "Usually a hook for JS or a typo in a class name.",
         """
         SELECT DISTINCT n.name FROM edges e JOIN nodes n ON n.id = e.dst
         WHERE e.kind = 'emits' AND n.kind = 'css_class'
           AND n.id NOT IN (SELECT dst FROM edges WHERE kind = 'styles')
         """),
        ("posted to or polled, but no such route",
         "A form or fetch aimed at a route that does not exist. This is a 404 "
         "waiting for someone to click it.",
         """
         SELECT DISTINCT n.name FROM edges e JOIN nodes n ON n.id = e.dst
         WHERE e.kind IN ('posts_to','polls') AND n.kind = 'route' AND n.file IS NULL
         """),
        ("template uses a name the handler never provides",
         "Jinja renders these as empty rather than failing, so they are quiet.",
         """
         SELECT DISTINCT n.name FROM edges e JOIN nodes n ON n.id = e.dst
         WHERE e.kind = 'needs'
           AND n.id NOT IN (SELECT dst FROM edges WHERE kind = 'provides')
         """),
        ("CSS variable used but never given a value",
         "Falls back to the default in var(), or to nothing at all.",
         """
         SELECT DISTINCT n.name FROM edges e JOIN nodes n ON n.id = e.dst
         WHERE e.kind = 'uses' AND n.kind = 'css_var'
           AND n.id NOT IN (SELECT dst FROM edges WHERE kind = 'defines' AND dst LIKE 'css_var:%')
         """),
        ("table declared but never queried",
         "Schema for something that no longer happens.",
         """
         SELECT DISTINCT n.name FROM edges e JOIN nodes n ON n.id = e.dst
         WHERE e.kind = 'declares'
           AND n.id NOT IN (SELECT dst FROM edges WHERE kind IN ('reads','writes'))
         """),
        ("db function nothing calls",
         "Either dead, or only reached from a template -- check before deleting.",
         """
         SELECT n.name FROM nodes n
         WHERE n.kind = 'function' AND n.name LIKE 'db.%'
           AND n.name NOT LIKE 'db._%'
           AND n.id NOT IN (SELECT dst FROM edges WHERE kind = 'calls')
         """),
    ]
    total = 0
    for title, why, sql in checks:
        rows = conn.execute(sql).fetchall()
        if not rows:
            continue
        total += len(rows)
        print(f"\n{title}  ({len(rows)})")
        print(f"  {why}")
        for r in rows:
            print(f"    {r['name']}")
    print(f"\n{total} finding(s)." if total else "\nNothing loose.")


def cmd_map(conn, _args):
    """A compact read of the whole thing."""
    print("# lykkepoller\n")
    print("## Routes\n")
    for r in conn.execute(
        "SELECT n.name, n.file, n.line FROM nodes n WHERE n.kind='route' AND n.file IS NOT NULL "
        "ORDER BY n.name"
    ):
        tables = conn.execute(
            "SELECT DISTINCT t.name, e2.kind FROM edges h JOIN edges e2 ON e2.src = h.dst "
            "JOIN nodes t ON t.id = e2.dst WHERE h.src = ? AND h.kind='handled_by' "
            "AND e2.kind IN ('reads','writes') ORDER BY t.name",
            (f"route:{r['name']}",)
        ).fetchall()
        note = "  " + ", ".join(f"{t['kind'][0]}:{t['name']}" for t in tables) if tables else ""
        print(f"- `{r['name']}`{note}")

    print("\n## Modules\n")
    for r in conn.execute(
        "SELECT n.name, COUNT(e.dst) fns FROM nodes n LEFT JOIN edges e "
        "ON e.src = n.id AND e.kind='defines' WHERE n.kind='module' AND n.file IS NOT NULL "
        "AND n.name NOT LIKE 'tests.%' GROUP BY n.name ORDER BY n.name"
    ):
        print(f"- `{r['name']}` — {r['fns']} definitions")

    print("\n## Tables\n")
    for r in conn.execute("SELECT name FROM nodes WHERE kind='table' ORDER BY name"):
        users = conn.execute(
            "SELECT DISTINCT n.name FROM edges e JOIN nodes n ON n.id = e.src "
            "WHERE e.dst = ? AND e.kind IN ('reads','writes') ORDER BY n.name",
            (f"table:{r['name']}",)
        ).fetchall()
        print(f"- `{r['name']}` — {len(users)} function(s)")


def cmd_sql(conn, args):
    try:
        rows = conn.execute(args.query).fetchall()
    except sqlite3.Error as e:
        sys.exit(f"sqlite: {e}")
    if not rows:
        print("(no rows)")
        return
    print(" | ".join(rows[0].keys()))
    for r in rows:
        print(" | ".join("" if v is None else str(v) for v in r))


def main():
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="scan the repo into tools/kg.sqlite")
    sub.add_parser("stats", help="what is in the graph")
    sub.add_parser("check", help="seams that have come apart")
    sub.add_parser("map", help="compact overview")
    for name, helptext in [("node", "one thing, both directions"),
                           ("trace", "a route, end to end")]:
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("term")
    sp = sub.add_parser("impact", help="what breaks if I change it")
    sp.add_argument("term")
    sp.add_argument("--depth", type=int, default=3)
    sp = sub.add_parser("sql", help="anything else")
    sp.add_argument("query")
    args = p.parse_args()

    if args.cmd == "build":
        g = build()
        print(f"{len(g.nodes)} nodes, {len(set(g.edges))} edges -> {rel(DB_PATH)}")
        return
    conn = connect()
    {"stats": cmd_stats, "node": cmd_node, "impact": cmd_impact, "trace": cmd_trace,
     "check": cmd_check, "map": cmd_map, "sql": cmd_sql}[args.cmd](conn, args)
    conn.close()


if __name__ == "__main__":
    main()
