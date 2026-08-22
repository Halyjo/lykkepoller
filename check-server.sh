#!/bin/bash
# Ask the server directly what it sends, with no browser involved.
B="${1:?usage: check.sh http://127.0.0.1:8000 TOKEN}"; T="${2:?need the admin token}"
J=$(mktemp)
curl -s -c "$J" -b "$J" -o /dev/null "$B/admin?token=$T"
echo "reject form in the HTML the server sends : $(curl -s -b "$J" "$B/admin" | grep -c 'admin/reject')   (want 1 or more)"
echo "reject route exists                      : $(curl -s -b "$J" -o /dev/null -w '%{http_code}' -X POST "$B/admin/reject" -d 'qid=x&rid=1&rejected=1')   (want 303, not 404)"
echo "app.js served has the x                  : $(curl -s "$B/static/app.js" | grep -c 'admin/reject')   (want 1)"
echo "app.js cache header                      : $(curl -sI "$B/static/app.js" | grep -i cache-control || echo 'NONE -> old server, restart it')"
