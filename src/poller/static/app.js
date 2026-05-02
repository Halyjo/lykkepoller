// app.js -- tiny vanilla JS. No framework.
//
// Two roles, dispatched from the body class set by the template:
//
//   1. body.participant -- participant page polls /api/participant/state
//      every ~1.5s. The poll doubles as a heartbeat (the server updates
//      last_seen_at). When the active question changes, or the session
//      ends, we replace the page body so the participant doesn't need to
//      hit refresh. (Implemented in M5.)
//
//   2. body.admin -- admin page polls /api/admin/state every ~1.5s for
//      response/participant counts and free-text moderation list.
//      Keyboard shortcuts (handled here) drive the state machine without
//      mouse clicks. (Polling implemented in M5; shortcuts are M3.)
//
//   3. body.present -- /present polls /api/present/state every ~1.5s
//      for the same counts and (when the reveal toggle is on) the
//      list of approved free-text answers. (Implemented in M5.)

const POLL_MS = 1500;

document.addEventListener("DOMContentLoaded", () => {
  if (document.body.classList.contains("admin")) {
    bindAdminShortcuts();
  }
});

// -- admin keyboard shortcuts -------------------------------------------------
//
// →/Space  next question (or end session if on the last)
// ←        previous question
// Esc      clear active question (back to IDLE)
// R        toggle "reveal free-text answers on /present"
// E        end session (with confirm)
//
// We submit the corresponding HTML form so the server-side handler is the
// single source of truth -- no parallel JS state. We also skip the shortcut
// when focus is in an input/textarea so typing in the override URL field
// doesn't trigger anything.
function bindAdminShortcuts() {
  document.addEventListener("keydown", (e) => {
    const tag = (document.activeElement && document.activeElement.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;

    const submit = (selector) => {
      const f = document.querySelector(selector);
      if (f) {
        e.preventDefault();
        f.submit();
      }
    };

    switch (e.key) {
      case "ArrowRight":
      case " ":
        submit('form[action="/admin/next"]');
        break;
      case "ArrowLeft":
        submit('form[action="/admin/prev"]');
        break;
      case "Escape":
        submit('form[action="/admin/clear"]');
        break;
      case "r":
      case "R":
        submit('form[action="/admin/reveal"]');
        break;
      case "e":
      case "E":
        submit('form[action="/admin/end"]');
        break;
    }
  });
}
