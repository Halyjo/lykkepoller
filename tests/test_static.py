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
    for page in ("pollParticipant", "pollDrive", "pollPresent"):
        assert f"startPolling({page})" in src, page


def test_drive_forms_post_without_navigating():
    """Clicking a control used to reload /drive and throw the scroll position
    away, which hurts most when approving an answer far down the list. Mouse
    clicks now take the same fetch path the keyboard shortcuts always did."""
    src = APP_JS.read_text()
    assert "function bindFormPosts" in src
    assert "bindFormPosts(refresh)" in src
    # A cancelled confirm() must not be resurrected, and a refused POST has to
    # become visible rather than vanish.
    assert "e.defaultPrevented" in src
    assert "form.submit()" in src


def test_participant_keeps_its_real_submit():
    """The reload is wanted on a phone: it is what redraws the form as locked."""
    src = APP_JS.read_text()
    drive_at = src.index('classList.contains("drive")')
    drive_branch = src[drive_at:]
    participant = src[src.index('classList.contains("participant")'):drive_at]
    assert "bindFormPosts" in drive_branch
    assert "bindFormPosts" not in participant


def test_polling_wakes_on_visibility():
    """The fix for phones that were locked mid-question."""
    src = APP_JS.read_text()
    assert "visibilitychange" in src
    assert "pageshow" in src
