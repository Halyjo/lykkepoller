"""app.js is never imported by Python, so nothing else notices if it breaks.

A syntax error there is silent and total: the browser parses nothing, so no
page polls, and the audience sits on a stale question until they reload by
hand. That happened once. Hence this test.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).parent.parent / "src" / "lykkepoller" / "static" / "app.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_app_js_parses():
    result = subprocess.run(
        ["node", "--check", str(APP_JS)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_app_js_polls_every_page_type():
    """Each page type must actually start its poll loop."""
    src = APP_JS.read_text()
    for page in ("pollParticipant", "pollAdmin", "pollPresent"):
        assert f"startPolling({page})" in src, page


def test_polling_wakes_on_visibility():
    """The fix for phones that were locked mid-question."""
    src = APP_JS.read_text()
    assert "visibilitychange" in src
    assert "pageshow" in src
