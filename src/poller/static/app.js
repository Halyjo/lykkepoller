// app.js — tiny vanilla JS for live polling.
//
// Two polling loops live here, both deliberately simple:
//   1. Participant page polls /api/participant/state every ~1.5s.
//      The poll doubles as a heartbeat: hitting that endpoint updates
//      the server's last_seen_at for this participant. When the active
//      question changes (or the session ends), we replace the page body.
//   2. Admin and /present poll /api/admin/state and /api/present/state
//      every ~1.5s for live response counts and (admin-only) free-text
//      moderation state.
//
// Keep this file small. No framework. Plain fetch() and DOM updates.
// Educational comments inline below.

// Filled in once the DOM is ready, depending on the page.
const POLL_MS = 1500;

document.addEventListener("DOMContentLoaded", () => {
  // Milestone 1 placeholder: the polling loops are wired up in M3/M5.
  // For now the page just renders server-side and doesn't auto-update.
});
