"""HTML-emitting helpers for slide authoring.

These are exposed as Jinja globals so a slide file can call them like macros
without an `{% import %}` boilerplate at the top:

    {{ bullets(["First", "Second", "Third"], title="Why metrics drift") }}
    {{ image("images/diagram.png", caption="Loss landscape") }}
    {{ math("L = -\\sum_i y_i \\log \\hat y_i") }}
    {{ table(["Model", "F1"], [["Baseline", 0.81], ["Boundary-aware", 0.74]]) }}
    {{ two_col(image("images/lhs.png"), bullets(["A", "B"])) }}
    {{ code_block("python", "for x in xs:\\n    print(x)") }}
    {{ quote("All models are wrong, some are useful.", by="George Box") }}

Plain HTML is always allowed too -- macros are convenience, not a wall.

For math, we rely on KaTeX auto-render, which scans the document for ``$...$``
and ``$$...$$`` delimiters. The ``math()`` macro just wraps the expression in
a styled block and emits ``$$...$$`` so KaTeX picks it up.

Image paths are resolved against the per-talk asset mount at ``/talk/...``.
"""

from markupsafe import Markup, escape


def _wrap(content) -> Markup:
    """Allow callers to pass either a raw string (escaped) or pre-rendered
    Markup (kept as-is). Used by layout macros (two_col, fragment) that wrap
    arbitrary content."""
    if isinstance(content, Markup):
        return content
    return escape(content)


def title_slide(title: str, subtitle: str | None = None, author: str | None = None) -> Markup:
    sub = f'<p class="slide-subtitle">{escape(subtitle)}</p>' if subtitle else ""
    author = f'<p class="slide-subtitle">{escape(author)}</p>' if author else ""
    return Markup(
        f'<section class="slide-hero"><h1>{escape(title)}</h1>{sub}{author}</section>'
    )


def bullets(items: list, *, title: str | None = None) -> Markup:
    head = f"<h2>{escape(title)}</h2>" if title else ""
    li = "".join(f"<li>{_wrap(x)}</li>" for x in items)
    return Markup(f'<section class="slide-bullets">{head}<ul>{li}</ul></section>')


def image(
    src: str,
    *,
    caption: str | None = None,
    alt: str = "",
    width: str | None = None,
) -> Markup:
    style = f' style="max-width: {escape(width)}"' if width else ""
    cap = f"<figcaption>{escape(caption)}</figcaption>" if caption else ""
    # Asset paths are served from /talk/. If the user gives an absolute URL or
    # already-prefixed path, keep it as-is.
    if src.startswith(("http://", "https://", "/")):
        href = src
    else:
        href = "/talk/" + src
    return Markup(
        f'<figure class="slide-image">'
        f'<img src="{escape(href)}" alt="{escape(alt)}"{style}>'
        f"{cap}</figure>"
    )


def math(expr: str, *, display: bool = True) -> Markup:
    """Wrap a TeX expression in a styled block. KaTeX auto-render picks it up."""
    delim = "$$" if display else "$"
    cls = "slide-math display" if display else "slide-math inline"
    return Markup(f'<div class="{cls}">{delim}{escape(expr)}{delim}</div>')


def table(headers: list, rows: list[list]) -> Markup:
    th = "".join(f"<th>{_wrap(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{_wrap(c)}</td>" for c in row) + "</tr>" for row in rows
    )
    return Markup(
        f'<table class="slide-table">'
        f"<thead><tr>{th}</tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def two_col(left, right, *, ratio: str = "1fr 1fr") -> Markup:
    return Markup(
        f'<div class="slide-two-col" style="grid-template-columns: {escape(ratio)}">'
        f"<div>{_wrap(left)}</div><div>{_wrap(right)}</div></div>"
    )


def code_block(language: str, source: str) -> Markup:
    return Markup(
        f'<pre class="slide-code language-{escape(language)}">'
        f"<code>{escape(source)}</code></pre>"
    )


def quote(text: str, *, by: str | None = None) -> Markup:
    cite = f'<cite>— {escape(by)}</cite>' if by else ""
    return Markup(f'<blockquote class="slide-quote">{escape(text)}{cite}</blockquote>')


def fragment(content) -> Markup:
    """Mark content as a click-to-reveal fragment.

    The deck controller (static/deck.js) hides every `.fragment` element by
    default and reveals them one at a time on `→` / Space, before advancing
    the slide.
    """
    return Markup(f'<div class="fragment">{_wrap(content)}</div>')


def callout(text, *, kind: str = "note") -> Markup:
    """A boxed aside. `kind` is one of: note, warn, good."""
    if kind not in ("note", "warn", "good"):
        kind = "note"
    return Markup(f'<aside class="slide-callout {kind}">{_wrap(text)}</aside>')


def jinja_globals() -> dict:
    """Convenient bundle for `env.globals.update(slide_macros.jinja_globals())`."""
    return {
        "title_slide": title_slide,
        "bullets": bullets,
        "image": image,
        "math": math,
        "table": table,
        "two_col": two_col,
        "code_block": code_block,
        "quote": quote,
        "fragment": fragment,
        "callout": callout,
    }
