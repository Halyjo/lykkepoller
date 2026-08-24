#!/usr/bin/env bash
# Drive a real session end to end, the way a presenter and a room would.
#
#   tools/smoke.sh [port] [quiz file]
#
# The quiz file may be a Python script or a saved .lykkepoll file; both are
# ways to start a session, so both are worth driving through a real server.
#
# pytest covers the app through TestClient, which never starts a server. This
# starts one. Everything TestClient stands in for has to actually work here:
# uvicorn, the static mount and its cache headers, cookies across redirects,
# the QR redraw, the CSV download.
#
# Every failure this has caught was invisible to the test suite: an app.js the
# browser would not parse, a QR still pointing at 127.0.0.1 after the tunnel
# came up, static files a browser would serve from cache for hours.
#
# Leaves nothing behind -- its session database is deleted on the way out,
# including when it fails.

set -uo pipefail
cd "$(dirname "$0")/.."

PORT="${1:-8912}"
QUIZ="${2:-quizzes/example_quiz.py}"
BASE="http://127.0.0.1:$PORT"

JAR=$(mktemp)      # the presenter's cookies
PHONE=$(mktemp)    # one member of the audience
LOG=$(mktemp)
CSV=$(mktemp)
PASS=0
FAIL=0
SERVER=""
DB=""

cleanup() {
  [ -n "$SERVER" ] && kill "$SERVER" 2>/dev/null
  [ -n "$DB" ] && rm -f "$DB" "${DB%.sqlite}".sqlite-shm "${DB%.sqlite}".sqlite-wal "${DB%.sqlite}".qr.png
  rm -f "$JAR" "$PHONE" "$LOG" "$CSV"
}
trap cleanup EXIT

check() {  # check <what> <expected> <actual>
  if [ "$2" = "$3" ]; then
    printf '  ok    %s\n' "$1"
    PASS=$((PASS + 1))
  else
    printf '  FAIL  %s\n          expected: %s\n          got:      %s\n' "$1" "$2" "$3"
    FAIL=$((FAIL + 1))
  fi
}

contains() {  # contains <what> <needle> <haystack>
  case "$3" in
    *"$2"*) printf '  ok    %s\n' "$1"; PASS=$((PASS + 1)) ;;
    *)      printf '  FAIL  %s (no %s)\n' "$1" "$2"; FAIL=$((FAIL + 1)) ;;
  esac
}

json() {  # json <path-expr>  -- reads stdin
  python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"
}

# --- start --------------------------------------------------------------------

echo "smoke: $QUIZ on port $PORT"
case "$QUIZ" in
  *.lykkepoll) uv run lykkepoller run --file "$QUIZ" --no-tunnel --port "$PORT" >"$LOG" 2>&1 & ;;
  *)           uv run "$QUIZ" --no-tunnel --port "$PORT" >"$LOG" 2>&1 & ;;
esac
SERVER=$!

for _ in $(seq 1 50); do
  curl -sf -o /dev/null "$BASE/qr.png" && break
  sleep 0.2
done
if ! curl -sf -o /dev/null "$BASE/qr.png"; then
  echo "  FAIL  server never answered. Its output:"
  sed 's/^/          /' "$LOG"
  exit 1
fi

SID=$(grep '^Session:' "$LOG" | awk '{print $2}')
TOKEN=$(grep -o 'admin?token=[a-z0-9-]*' "$LOG" | head -1 | cut -d= -f2)
DB=$(grep '^Database:' "$LOG" | awk '{print $2}')
echo "  session $SID"

# --- the presenter gets in ----------------------------------------------------

echo "presenter"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -c "$JAR" -b "$JAR" "$BASE/admin?token=$TOKEN")
check "token redirects and sets a cookie" "303" "$CODE"
check "admin is refused without one" "401" \
  "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/admin")"

# --- static assets ------------------------------------------------------------

echo "static"
JS_URL=$(curl -s -b "$JAR" "$BASE/admin" | grep -o '/static/app\.js?v=[a-f0-9]*' | head -1)
contains "the page asks for a versioned app.js" "?v=" "$JS_URL"
contains "app.js is served and current" "admin/reject" "$(curl -s "$BASE$JS_URL")"
contains "and must be revalidated" "no-cache" "$(curl -sI "$BASE$JS_URL" | tr -d '\r')"

# --- a multiple-choice question -----------------------------------------------

echo "multiple choice"
curl -s -b "$JAR" -X POST "$BASE/admin/next" -o /dev/null
check "opens hidden, so the room cannot follow the leader" "False" \
  "$(curl -s -b "$JAR" "$BASE/api/admin/state" | json 'd["reveal_free_text"]')"

curl -s -c "$PHONE" -b "$PHONE" -o /dev/null "$BASE/join/$SID"
curl -s -b "$PHONE" -X POST "$BASE/answer/$SID" -d "question_id=q1&answer=B" -o /dev/null
curl -s -b "$PHONE" -X POST "$BASE/answer/$SID" -d "question_id=q1&answer=A" -o /dev/null
check "one answer each, changes ignored" "1" \
  "$(curl -s "$BASE/api/present/state" | json 'd["active_results"]["total"]')"

curl -s -b "$JAR" -X POST "$BASE/admin/reveal_correct" -d "on=1" -o /dev/null
contains "showing the answer marks it correct" 'bar correct' "$(curl -s "$BASE/present")"

# --- free text ----------------------------------------------------------------

echo "free text"
curl -s -b "$JAR" -X POST "$BASE/admin/activate" -d "qid=q2" -o /dev/null
check "opens revealed, because it opens empty" "True" \
  "$(curl -s -b "$JAR" "$BASE/api/admin/state" | json 'd["reveal_free_text"]')"

for text in "keep this" "spam spam" "keep this too"; do
  P=$(mktemp)
  curl -s -c "$P" -b "$P" -o /dev/null "$BASE/join/$SID"
  curl -s -b "$P" -X POST "$BASE/answer/$SID" --data-urlencode "question_id=q2" \
       --data-urlencode "answer=$text" -o /dev/null
  rm -f "$P"
done
check "three answers in" "3" \
  "$(curl -s -b "$JAR" "$BASE/api/admin/state" | json 'len(d["results"]["q2"]["answers"])')"

BAD=$(curl -s -b "$JAR" "$BASE/api/admin/state" \
      | json 'next(a["id"] for a in d["results"]["q2"]["answers"] if "spam" in a["answer"])')
curl -s -b "$JAR" -X POST "$BASE/admin/reject" -d "qid=q2&rid=$BAD&rejected=1" -o /dev/null
curl -s -b "$JAR" -X POST "$BASE/admin/approve_all" -o /dev/null
PRESENT=$(curl -s "$BASE/present")
contains "the good ones reach the projector" "keep this too" "$PRESENT"
case "$PRESENT" in
  *"spam spam"*) echo "  FAIL  a crossed-out answer reached the projector"; FAIL=$((FAIL + 1)) ;;
  *)             echo "  ok    approve-all skipped the crossed-out one"; PASS=$((PASS + 1)) ;;
esac
contains "answers render as cards" 'class="answer-cards"' "$PRESENT"
contains "the card count drives their size" "--answer-count: 2" "$PRESENT"

# --- the join address ---------------------------------------------------------

echo "join address"
QR_BEFORE=$(curl -s "$BASE/qr.png" | md5)
curl -s -b "$JAR" -X POST "$BASE/admin/override" -d "url=https://smoke.example" -o /dev/null
check "the poll carries the new address" "https://smoke.example/join/$SID" \
  "$(curl -s "$BASE/api/present/state" | json 'd["join_url"]')"
QR_AFTER=$(curl -s "$BASE/qr.png" | md5)
if [ "$QR_BEFORE" != "$QR_AFTER" ]; then
  echo "  ok    the QR is redrawn for it"; PASS=$((PASS + 1))
else
  echo "  FAIL  the QR did not change with the address"; FAIL=$((FAIL + 1))
fi

# --- getting the answers out --------------------------------------------------

echo "afterwards"
curl -s -b "$JAR" "$BASE/admin/export.csv" -o "$CSV"
check "one header and four answers" "5" "$(wc -l < "$CSV" | tr -d ' ')"
contains "multiple choice carries its label" "To give feedback to the model" "$(cat "$CSV")"
check "the CSV needs the admin cookie" "401" \
  "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/admin/export.csv")"

curl -s -b "$JAR" -X POST "$BASE/admin/end" -o /dev/null
check "ending the session" "ended" "$(curl -s "$BASE/api/present/state" | json 'd["phase"]')"

# --- reopening ----------------------------------------------------------------

echo "reopen"
kill "$SERVER" 2>/dev/null; wait "$SERVER" 2>/dev/null; SERVER=""
REOPEN=$(uv run lykkepoller inspect "$DB")
contains "the source file is recorded" "$(basename "$QUIZ")" "$REOPEN"
check "and every answer survived" "4" "$(echo "$REOPEN" | awk '/^Answers:/{print $2}')"

# --- report -------------------------------------------------------------------

echo
if [ "$FAIL" -eq 0 ]; then
  echo "$PASS ok."
  exit 0
fi
echo "$PASS ok, $FAIL failed."
exit 1
