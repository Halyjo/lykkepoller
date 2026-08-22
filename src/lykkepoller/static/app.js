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

// Poll on a self-scheduling timer, not setInterval.
//
// Two things setInterval gets wrong on a phone:
//
//   1. A locked screen or a switch to another app throttles the timer to
//      once a minute (Chrome) or suspends it outright (iOS Safari). The
//      student looks back and is still on the previous question, which is
//      why reloading by hand "fixed" it. So we poll the instant the page
//      becomes visible again, and on pageshow, which also covers coming
//      back through the bfcache after a tap on "back".
//
//   2. setInterval fires whether or not the last poll finished. On a slow
//      connection that stacks requests until the browser's six-per-host
//      limit queues them. Scheduling the next poll only after the last one
//      lands cannot pile up.
function startPolling(poll) {
  let timer = null;
  let inFlight = false;

  async function tick() {
    if (inFlight) return;   // the running poll will schedule the next one
    inFlight = true;
    try {
      await poll();
    } catch (_e) {
      // Never let one failed poll kill the loop.
    } finally {
      inFlight = false;
      clearTimeout(timer);
      timer = setTimeout(tick, POLL_MS);
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") tick();
  });
  window.addEventListener("pageshow", () => tick());

  tick();
  return tick;
}

document.addEventListener("DOMContentLoaded", () => {
  if (document.body.classList.contains("participant")) {
    startPolling(pollParticipant);
  } else if (document.body.classList.contains("admin")) {
    bindAdminShortcuts(startPolling(pollAdmin), {onPresent: false});
  } else if (document.body.classList.contains("present")) {
    const root = document.getElementById("present-app");
    const refresh = startPolling(pollPresent);
    if (root && root.dataset.admin === "1") {
      bindAdminShortcuts(refresh, {onPresent: true});
    }
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
  const root = document.getElementById("participant-app");
  if (!root) return;
  const sessionId = root.dataset.sessionId;
  const data = await fetchJson("/api/participant/state/" + sessionId);
  if (!data) return;

  const currentPhase = root.dataset.phase || "";
  const currentQid = root.dataset.qid || "";
  const currentPriorMc = root.dataset.priorMc || "";
  const nextPhase = data.phase;
  const nextQid =
    data.phase === "active" && data.active_question ? data.active_question.id : "";
  const nextPriorMc = data.prior_mc_answer || "";

  // Only reload on transitions that change the rendered structure. Staying on
  // the same active question (counts ticking up server-side) does not affect
  // what the participant sees, so we leave their form alone -- in particular,
  // we do not clobber half-typed text in a free-text answer.
  // Exception: if a fresh MC answer just got recorded for this participant
  // (data-prior-mc went from "" to a value), we reload so the form locks --
  // matters when the participant has multiple tabs open or submits via a
  // path that didn't full-reload.
  if (
    currentPhase !== nextPhase ||
    currentQid !== nextQid ||
    (!currentPriorMc && nextPriorMc)
  ) {
    window.location.reload();
  }
}

// --- admin ------------------------------------------------------------------

async function pollAdmin() {
  const data = await fetchJson("/api/admin/state");
  if (!data) return;

  setText("connected-count", data.connected_count);
  setText("answered-count", data.answered_count);

  // answered-section visibility
  const answeredSection = document.getElementById("answered-section");
  if (answeredSection) answeredSection.style.display = data.active_question_id ? "" : "none";

  // phase badge
  const badge = document.getElementById("phase-badge");
  if (badge) {
    const [cls, text] = data.phase === "ended" ? ["ended", "ENDED"]
      : data.active_question_id ? ["active", "ACTIVE"]
      : ["idle", "IDLE"];
    badge.className = `badge ${cls}`;
    badge.textContent = text;
  }

  // The active question is the one accepting answers, and the one whose
  // reveal state the R and C badges describe.
  document.querySelectorAll(".question-item").forEach(item => {
    const isActive = item.dataset.qid === data.active_question_id;
    const r = data.results ? data.results[item.dataset.qid] : null;
    const hasCorrect = !!(r && r.type === "multiple_choice" && r.any_correct);
    item.classList.toggle("active", isActive);
    item.classList.toggle("answering", isActive);
    item.classList.toggle("revealed", isActive && !!data.reveal_free_text);
    item.classList.toggle("correct-revealed", isActive && !!data.reveal_correct && hasCorrect);

    const btn = item.querySelector('form[action="/admin/activate"] button');
    if (btn) btn.textContent = isActive ? "Open" : "Activate";

    // Reveal-state badges (only on the active item):
    //   .replies — whether free-text/MC results are visible on /present (R)
    //   .correct — whether the correct MC option is highlighted on /present (C);
    //              only relevant when the question is MC with a correct option.
    const head = item.querySelector(".q-head");
    const typeSpan = head ? head.querySelector(".muted") : null;
    const ensureBadge = (cls, after) => {
      let b = item.querySelector(`.reveal-badge.${cls}`);
      if (!b && head) {
        b = document.createElement("span");
        if (after && after.parentNode === head) after.after(b);
        else head.appendChild(b);
      }
      return b;
    };
    const removeBadge = (cls) => {
      const b = item.querySelector(`.reveal-badge.${cls}`);
      if (b) b.remove();
    };

    if (isActive) {
      const replies = ensureBadge("replies", typeSpan);
      const rOn = !!data.reveal_free_text;
      replies.className = "reveal-badge replies " + (rOn ? "on" : "off");
      replies.textContent = rOn ? "Showing on /present" : "Hidden on /present";
      if (hasCorrect) {
        const correct = ensureBadge("correct", replies);
        const cOn = !!data.reveal_correct;
        correct.className = "reveal-badge correct " + (cOn ? "on" : "off");
        correct.textContent = cOn ? "Correct shown" : "Correct hidden";
      } else {
        removeBadge("correct");
      }
    } else {
      removeBadge("replies");
      removeBadge("correct");
    }
  });

  // reveal free-text toggle button
  const revealFTForm = document.querySelector('form[action="/admin/reveal"]');
  if (revealFTForm) {
    const inp = revealFTForm.querySelector('input[name="on"]');
    if (inp) inp.value = data.reveal_free_text ? "0" : "1";
    const btn = revealFTForm.querySelector("button");
    if (btn) btn.textContent = (data.reveal_free_text ? "Hide" : "Reveal") + " answers on /present (R)";
  }

  // reveal correct toggle button
  const revealCorrForm = document.querySelector('form[action="/admin/reveal_correct"]');
  if (revealCorrForm) {
    const inp = revealCorrForm.querySelector('input[name="on"]');
    if (inp) inp.value = data.reveal_correct ? "0" : "1";
    const btn = revealCorrForm.querySelector("button");
    if (btn) btn.textContent = (data.reveal_correct ? "Hide" : "Show") + " correct (C)";
  }

  for (const [qid, r] of Object.entries(data.results)) {
    const c = document.querySelector(`.q-results[data-qid="${cssEscape(qid)}"]`);
    if (!c) continue;
    if (r.type === "multiple_choice") {
      const isActive = qid === data.active_question_id;
      const showCorrect = data.reveal_correct && isActive && r.any_correct;
      c.innerHTML = renderMCBars(r, showCorrect);
    } else if (r.type === "rating") {
      c.innerHTML = renderRatingBars(r);
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

function renderRatingBars(r) {
  let html = "";
  if (r.total) {
    for (const b of r.buckets) {
      const endLabel =
        b.step === 1 ? ` <span class="muted">(${escapeHtml(r.low_label)})</span>` :
        b.step === r.steps ? ` <span class="muted">(${escapeHtml(r.high_label)})</span>` : "";
      html +=
        `<div class="bar">` +
        `<div class="bar-label">${b.step}${endLabel}</div>` +
        `<div class="bar-fill" style="width: ${b.pct}%"></div>` +
        `<div class="bar-count">${b.count} · ${b.pct}%</div>` +
        `</div>`;
    }
  }
  const avg = (r.average != null) ? ` · avg ${r.average}` : "";
  html += `<p class="muted">${r.total} response${r.total === 1 ? "" : "s"}${avg}</p>`;
  return html;
}

function renderFreeTextList(r, qid) {
  // Must stay in step with the same block in admin.html -- both render the
  // moderation list, one on load and one on every poll.
  let html = `<p class="muted">${r.total} response${r.total === 1 ? "" : "s"}</p>`;
  if (r.answers.length) {
    html += '<ul class="free-text-list">';
    for (const a of r.answers) {
      const cls = a.rejected ? "rejected" : a.approved ? "approved" : "";
      const hidden =
        `<input type="hidden" name="qid" value="${escapeHtml(qid)}">` +
        `<input type="hidden" name="rid" value="${a.id}">`;
      html +=
        `<li class="${cls}">` +
        `<span class="answer-text">${escapeHtml(a.answer)}</span>` +
        `<form method="post" action="/admin/reject" class="inline">` +
        hidden +
        `<input type="hidden" name="rejected" value="${a.rejected ? "0" : "1"}">` +
        `<button type="submit" class="reject-btn" title="${
          a.rejected ? "Put this answer back" : "Cross this answer out"
        }">${a.rejected ? "\u21ba" : "\u2715"}</button>` +
        `</form>`;
      if (!a.rejected) {
        html +=
          `<form method="post" action="/admin/approve" class="inline">` +
          hidden +
          `<input type="hidden" name="approved" value="${a.approved ? "0" : "1"}">` +
          `<button type="submit">${a.approved ? "Unapprove" : "Approve"}</button>` +
          `</form>`;
      }
      html += `</li>`;
    }
    html += "</ul>";
  }
  return html;
}

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
  // Reload on a structural change -- a new phase or a new question -- so the
  // server renders the new page. Counts and result bars update in place.
  if (currentPhase !== data.phase || currentQid !== nextQid) {
    window.location.reload();
    return;
  }

  // Keep the hidden reveal-toggle forms in sync so a second R/C press sends
  // the correct flip value rather than a stale one.
  syncRevealToggle('form[action="/admin/reveal"]', data.reveal_free_text);
  syncRevealToggle('form[action="/admin/reveal_correct"]', data.reveal_correct);

  const results = document.getElementById("present-results");
  if (results && data.phase === "active" && data.active_results) {
    if (data.active_results.type === "multiple_choice") {
      // Bars are visible when R OR C is on (matches present.html). C alone
      // is enough -- if the presenter wants to show the correct answer, it
      // would be silly to require also pressing R first.
      const showBars = data.reveal_free_text || data.reveal_correct;
      const showCorrect = data.reveal_correct && data.active_results.any_correct;
      results.innerHTML = renderMCPresent(data.active_results, showBars, showCorrect);
    } else if (data.active_results.type === "rating") {
      results.innerHTML = renderRatingPresent(data.active_results, data.reveal_free_text);
    } else {
      results.innerHTML = renderFreeTextPresent(data.active_results, data.reveal_free_text);
    }
  }
}

function syncRevealToggle(selector, currentlyOn) {
  const form = document.querySelector(selector);
  if (!form) return;
  const inp = form.querySelector('input[name="on"]');
  if (inp) inp.value = currentlyOn ? "0" : "1";
}

function renderMCPresent(r, showBars, showCorrect) {
  let html = `<p class="big">${r.total} response${r.total === 1 ? "" : "s"} received</p>`;
  // Option labels are always rendered; .unrevealed hides the fill+count.
  for (const opt of r.options) {
    let cls = showBars && showCorrect ? (opt.is_correct ? "correct" : "dimmed") : "";
    if (!showBars) cls += " unrevealed";
    html +=
      `<div class="bar ${cls}">` +
      `<div class="bar-label">${escapeHtml(opt.label)}</div>` +
      `<div class="bar-fill" style="width: ${opt.pct}%"></div>` +
      `<div class="bar-count">${opt.count} (${opt.pct}%)</div>` +
      `</div>`;
  }
  return html;
}

function renderRatingPresent(r, reveal) {
  const avg = (reveal && r.average != null) ? ` · avg ${r.average}` : "";
  let html = `<p class="big">${r.total} response${r.total === 1 ? "" : "s"} received${avg}</p>`;
  for (const b of r.buckets) {
    const endLabel =
      b.step === 1 ? ` <span class="muted">(${escapeHtml(r.low_label)})</span>` :
      b.step === r.steps ? ` <span class="muted">(${escapeHtml(r.high_label)})</span>` : "";
    const cls = reveal ? "bar" : "bar unrevealed";
    html +=
      `<div class="${cls}">` +
      `<div class="bar-label">${b.step}${endLabel}</div>` +
      `<div class="bar-fill" style="width: ${b.pct}%"></div>` +
      `<div class="bar-count">${b.count} (${b.pct}%)</div>` +
      `</div>`;
  }
  return html;
}

function renderFreeTextPresent(r, reveal) {
  let html = `<p class="big">${r.total} response${r.total === 1 ? "" : "s"} received</p>`;
  if (reveal && r.approved_answers && r.approved_answers.length) {
    html += '<div class="answer-cards">';
    for (const a of r.approved_answers) {
      html += `<blockquote class="answer-card">${escapeHtml(a.answer)}</blockquote>`;
    }
    html += "</div>";
  }
  return html;
}

function bindAdminShortcuts(refresh, opts) {
  // refresh: function called after each successful POST to re-fetch state.
  //   pollAdmin on /admin, pollPresent on /present.
  // opts.onPresent: skip /admin-only behaviors (E to end, scroll-into-view).
  //   Dropping E on /present avoids accidentally ending the session from the
  //   audience-facing screen.
  const onPresent = !!(opts && opts.onPresent);
  document.addEventListener("keydown", (e) => {
    const tag = ((document.activeElement && document.activeElement.tagName) || "").toLowerCase();
    if (tag === "input" || tag === "textarea") return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;

    const submit = (selector) => {
      const f = document.querySelector(selector);
      if (!f) return;
      e.preventDefault();
      fetch(f.action, {method: "POST", body: new FormData(f), credentials: "same-origin"})
        .then(() => refresh())
        .then(() => {
          if (onPresent) return;
          const active = document.querySelector(".question-item.active");
          if (active) active.scrollIntoView({behavior: "smooth", block: "nearest"});
        });
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
        if (!onPresent) submit('form[action="/admin/end"]');
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
