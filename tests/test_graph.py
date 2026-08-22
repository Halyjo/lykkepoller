"""Structural checks, run off the knowledge graph in tools/kg.py.

Only the two questions the graph answers with certainty live here. The fuzzier
ones -- dead CSS, unstyled classes -- stay in `kg.py check`, where a human
reads them and decides. A test that cries wolf gets ignored, and then so do
the real ones.

Both of these would have caught real breakage during the slide removal.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
import kg  # noqa: E402


@pytest.fixture(scope="module")
def graph():
    g = kg.Graph()
    defined = kg.scan_python(g)
    kg.resolve_calls(g, defined)
    kg.scan_templates(g)
    kg.scan_js(g)
    kg.scan_css(g)
    return g


def _edges(g, kind):
    return [e for e in g.edges if e[2] == kind]


def test_the_graph_finds_the_repo(graph):
    """A silent extraction failure would make every check below pass."""
    kinds = {n["kind"] for n in graph.nodes.values()}
    assert {"module", "function", "route", "table", "template", "css_class"} <= kinds
    assert len([n for n in graph.nodes.values() if n["kind"] == "route"]) > 15


def test_every_form_and_fetch_hits_a_real_route(graph):
    """A form action or fetch aimed at a route that no longer exists is a 404
    the presenter finds by clicking it mid-talk."""
    real = {n["id"] for n in graph.nodes.values()
            if n["kind"] == "route" and n["file"] is not None}
    broken = sorted({
        f"{src} -> {dst}  ({file}:{line})"
        for src, dst, kind, file, line in graph.edges
        if kind in ("posts_to", "polls") and dst not in real
    })
    assert not broken, "these point at no route:\n  " + "\n  ".join(broken)


def test_every_route_has_a_handler(graph):
    handled = {src for src, _, kind, _, _ in graph.edges if kind == "handled_by"}
    orphans = sorted(n["name"] for n in graph.nodes.values()
                     if n["kind"] == "route" and n["file"] and n["id"] not in handled)
    assert not orphans, f"routes with no handler: {orphans}"


def test_app_js_only_looks_for_markup_that_exists(graph):
    """A selector left behind when its markup was removed matches nothing and
    says nothing. That is how the slide-removal bugs stayed hidden."""
    drawn = {dst for _, dst, kind, _, _ in graph.edges if kind == "emits"}
    dangling = sorted({
        dst.split(":", 1)[1]
        for _, dst, kind, _, _ in graph.edges
        if kind == "queries" and dst not in drawn
    })
    assert not dangling, f"app.js queries classes nothing renders: {dangling}"


def test_every_table_in_the_schema_is_used(graph):
    used = {dst for _, dst, kind, _, _ in graph.edges if kind in ("reads", "writes")}
    declared = {dst for _, dst, kind, _, _ in graph.edges if kind == "declares"}
    unused = sorted(t.split(":", 1)[1] for t in declared - used)
    assert not unused, f"tables declared but never queried: {unused}"
