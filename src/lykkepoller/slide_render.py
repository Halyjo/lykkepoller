"""Render slide HTML files through Jinja with the slide-macro globals.

Done at session-creation time so the snapshot stored in SQLite is the final
HTML to inject into /present. This mirrors how questions are snapshotted:
the YAML/HTML files are the source, the DB row is the source of truth once a
session has started.
"""

from jinja2 import Environment

from . import slide_macros


def render_slides(slides: list[dict]) -> list[dict]:
    """Render the `html` field of every content slide through Jinja.

    Question slides pass through unchanged. Returns a new list (does not
    mutate the input).
    """
    env = Environment(autoescape=False)  # users author trusted HTML; macros escape user values
    env.globals.update(slide_macros.jinja_globals())

    out: list[dict] = []
    for s in slides:
        if s.get("type") == "content":
            template = env.from_string(s["html"])
            rendered = template.render()
            out.append({**s, "html": rendered})
        else:
            out.append(dict(s))
    return out
