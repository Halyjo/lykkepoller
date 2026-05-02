// app.js -- tiny vanilla JS. No framework.
//
// What this file does, by page:
//
//   body.participant
//     Polls /api/participant/state every ~1.5s. The poll doubles as a
//     heartbeat: the server updates participants.last_seen_at so it can
//     tell the presenter how many phones are currently connected.
//     If the active-question id changes, or the session ends, we reload
//     the page so the server template renders the new state. Reloading
//     keeps client-side templating out of the picture.
//
//   body.admin
//     Polls /api/admin/state every ~1.5s. Updates the connected/answered
//     counts and re-renders each question's result block in place
//     (MC bars or free-text moderation list). Also wires keyboard
//     shortcuts that submit the matching <form>, so server-side handlers
//     stay the single source of truth.
//
//       ->/Space  next question (or end session if on the last)
//       <-        previous question
//       Esc       clear active question (back to IDLE)
//       R         toggle "reveal free-text answers on /present"
//       C         toggle "show correct answer" (color the right MC option green,
//                 dim the others). Resets to off when the next question activates.
//       A         approve all currently-submitted free-text answers for the
//                 active question. Idempotent -- press it again as new answers
//                 come in to bulk-accept the new ones too.
//       E         end session (with confirm)
//
//     Shortcuts are skipped when focus is in an input/textarea so typing
//     in the override URL field does not trigger them.
//
//   body.present
//     Polls /api/present/state every ~1.5s. Updates connected/answered
//     and the result block; on phase or active-question changes, reloads.

const POLL_MS = 1500;

document.addEventListener("DOMContentLoaded", () => {
  if (document.body.classList.contains("participant")) {
    pollParticipant();
    setInterval(pollParticipant, POLL_MS);
  } else if (document.body.classList.contains("admin")) {
    bindAdminShortcuts();
    pollAdmin();
    setInterval(pollAdmin, POLL_MS);
  } else if (document.body.classList.contains("present")) {
    pollPresent();
    setInterval(pollPresent, POLL_MS);
  }
});

// --- helpers ----------------------------------------------------------------

function setText(id, value) {
  const el = document.getElementById(id);
  if (el != null) el.textContent = String(value);
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function fetchJson(url) {
  try {
    const res = await fetch(url, {credentials: "same-origin"});
    if (!res.ok) return null;
    return await res.json();
  } catch (_e) {
    return null;
  }
}

// --- participant ------------------------------------------------------------

async function pollParticipant() {
  const data = await fetchJson("/api/participant/state");
  if (!data) return;
  const root = document.getElementById("participant-app");
  if (!root) return;

  const currentPhase = root.dataset.phase || "";
  const currentQid = root.dataset.qid || "";
  const nextPhase = data.phase;
  const nextQid =
    data.phase === "active" && data.active_question ? data.active_question.id : "";

  // Only reload on transitions that change the rendered structure. Staying on
  // the same active question (counts ticking up server-side) does not affect
  // what the participant sees, so we leave their form alone -- in particular,
  // we do not clobber half-typed text in a free-text answer.
  if (currentPhase !== nextPhase || currentQid !== nextQid) {
    window.location.reload();
  }
}

// --- admin ------------------------------------------------------------------

async function pollAdmin() {
  const data = await fetchJson("/api/admin/state");
  if (!data) return;

  setText("connected-count", data.connected_count);
  setText("answered-count", data.answered_count);

  for (const [qid, r] of Object.entries(data.results)) {
    const c = document.querySelector(`.q-results[data-qid="${cssEscape(qid)}"]`);
    if (!c) continue;
    if (r.type === "multiple_choice") {
      // Correctness colors only apply to the active question -- past
      // questions are not retroactively repainted.
      const isActive = qid === data.active_question_id;
      const showCorrect = data.reveal_correct && isActive && r.any_correct;
      c.innerHTML = renderMCBars(r, showCorrect);
    } else {
      c.innerHTML = renderFreeTextList(r, qid);
    }
  }
}

function renderMCBars(r, showCorrect) {
  let html = "";
  if (r.total) {
    for (const opt of r.options) {
      const cls = showCorrect ? (opt.is_correct ? "correct" : "dimmed") : "";
      html +=
        `<div class="bar ${cls}">` +
        `<div class="bar-label">${escapeHtml(opt.label)}</div>` +
        `<div class="bar-fill" style="width: ${opt.pct}%"></div>` +
        `<div class="bar-count">${opt.count} · ${opt.pct}%</div>` +
        `</div>`;
    }
  }
  html += `<p class="muted">${r.total} response${r.total === 1 ? "" : "s"}</p>`;
  return html;
}

function renderFreeTextList(r, qid) {
  let html = `<p class="muted">${r.total} response${r.total === 1 ? "" : "s"}</p>`;
  if (r.answers.length) {
    html += '<ul class="free-text-list">';
    for (const a of r.answers) {
      const newApproved = a.approved ? "0" : "1";
      const action = a.approved ? "Unapprove" : "Approve";
      const cls = a.approved ? "approved" : "";
      html +=
        `<li class="${cls}">` +
        `<span class="answer-text">${escapeHtml(a.answer)}</span>` +
        `<form method="post" action="/admin/approve" class="inline">` +
        `<input type="hidden" name="qid" value="${escapeHtml(qid)}">` +
        `<input type="hidden" name="rid" value="${a.id}">` +
        `<input type="hidden" name="approved" value="${newApproved}">` +
        `<button type="submit">${action}</button>` +
        `</form>` +
        `</li>`;
    }
    html += "</ul>";
  }
  return html;
}

// CSS.escape isn't available everywhere; quick fallback for our use (ids are
// well-behaved YAML keys, but be defensive).
function cssEscape(s) {
  if (window.CSS && CSS.escape) return CSS.escape(s);
  return String(s).replace(/(["\\])/g, "\\$1");
}

// --- present ----------------------------------------------------------------

async function pollPresent() {
  const data = await fetchJson("/api/present/state");
  if (!data) return;

  setText("connected-count", data.connected_count);
  setText("answered-count", data.answered_count);

  const root = document.getElementById("present-app");
  if (!root) return;

  const currentPhase = root.dataset.phase || "";
  const currentQid = root.dataset.qid || "";
  const nextQid =
    data.phase === "active" && data.active_question ? data.active_question.id : "";
  if (currentPhase !== data.phase || currentQid !== nextQid) {
    window.location.reload();
    return;
  }

  if (data.phase === "active" && data.active_results) {
    const results = document.getElementById("present-results");
    if (!results) return;
    if (data.active_results.type === "multiple_choice") {
      const showCorrect = data.reveal_correct && data.active_results.any_correct;
      results.innerHTML = renderMCPresent(data.active_results, showCorrect);
    } else {
      results.innerHTML = renderFreeTextPresent(data.active_results, data.reveal_free_text);
    }
  }
}

function renderMCPresent(r, showCorrect) {
  let html = "";
  for (const opt of r.options) {
    const cls = showCorrect ? (opt.is_correct ? "correct" : "dimmed") : "";
    html +=
      `<div class="bar ${cls}">` +
      `<div class="bar-label">${escapeHtml(opt.label)}</div>` +
      `<div class="bar-fill" style="width: ${opt.pct}%"></div>` +
      `<div class="bar-count">${opt.count} (${opt.pct}%)</div>` +
      `</div>`;
  }
  return html;
}

function renderFreeTextPresent(r, reveal) {
  let html = `<p class="big">${r.total} response${r.total === 1 ? "" : "s"} received</p>`;
  if (reveal && r.approved_answers && r.approved_answers.length) {
    html += '<ul class="present-free-text">';
    for (const a of r.approved_answers) {
      html += `<li>${escapeHtml(a.answer)}</li>`;
    }
    html += "</ul>";
  }
  return html;
}

// --- admin keyboard shortcuts ----------------------------------------------

function bindAdminShortcuts() {
  document.addEventListener("keydown", (e) => {
    const tag = ((document.activeElement && document.activeElement.tagName) || "").toLowerCase();
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
      case "c":
      case "C":
        submit('form[action="/admin/reveal_correct"]');
        break;
      case "a":
      case "A":
        submit('form[action="/admin/approve_all"]');
        break;
    }
  });
}
