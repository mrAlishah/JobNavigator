#!/bin/bash
# Pixel + style gate with the scheduler paused, so a diff is a code diff.
# Usage: bash tests/tools/gate.sh <stage> [<base-stage>] [--theme X]
# Pauses scrape/email intervals, waits for active runs, builds nothing (build first),
# captures shots + crawl, diffs against <base-stage> when given, restores the intervals.
set -e
DOCKER="/c/Program Files/Docker/Docker/resources/bin/docker.exe"; export MSYS_NO_PATHCONV=1
KEY=pick-a-password; API=http://localhost/api
# With two or more accounts the key alone selects none (401 account_required): act as the owner
# through a signed X-Account-Token, as the harness does (JN_OWNER_PIN only when the owner has a PIN).
OWNER=$(curl -s -H "X-API-Key: $KEY" $API/auth/accounts | py -c "import json,sys; a=json.load(sys.stdin); print(([x for x in a if x.get('role')=='owner'] or a)[0]['id'])")
TOKEN=$(curl -s -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" $API/auth/verify-pin   -d "{\"account_id\":\"$OWNER\"${JN_OWNER_PIN:+,\"pin\":\"$JN_OWNER_PIN\"}}" | py -c "import json,sys; print(json.load(sys.stdin)['token'])")
AUTH=(-H "X-API-Key: $KEY" -H "X-Account-Token: $TOKEN")
STAGE=$1; BASE=$2; THEME=""
[ "$BASE" = "--theme" ] && { THEME="--theme $3"; BASE=""; }
[ "$3" = "--theme" ] && THEME="--theme $4"
for f in h.py shots.py stylecrawl.py shotdiff.py stylediff.py; do "$DOCKER" compose cp tests/tools/$f backend:/tmp/v2t/$f >/dev/null; done
SAVED=$(curl -s "${AUTH[@]}" $API/settings | py -c "import json,sys; d=json.load(sys.stdin); d=d.get('settings',d); print(json.dumps({k:d[k] for k in ('scrape_interval_minutes','email_check_interval_minutes') if k in d}))")
echo "saved intervals: $SAVED"
curl -s -X PATCH "${AUTH[@]}" -H "Content-Type: application/json" $API/settings -d '{"scrape_interval_minutes":0,"email_check_interval_minutes":0}' >/dev/null
for i in $(seq 1 60); do
  n=$(curl -s "${AUTH[@]}" $API/monitor/active | py -c "import json,sys; d=json.load(sys.stdin); print(len(d if isinstance(d,list) else d.get('active',d.get('items',[]))))")
  [ "$n" = "0" ] && break; sleep 5
done
echo "active runs: $n"
"$DOCKER" compose exec -T backend python /tmp/v2t/shots.py $STAGE $THEME 2>&1 | tail -1
"$DOCKER" compose exec -T backend python /tmp/v2t/stylecrawl.py $STAGE $THEME 2>&1 | tail -1
curl -s -X PATCH "${AUTH[@]}" -H "Content-Type: application/json" $API/settings -d "$SAVED" >/dev/null
echo "intervals restored"
mkdir -p tests/artifacts/design
"$DOCKER" compose cp backend:/tmp/v2t/shots/$STAGE tests/artifacts/design/$STAGE >/dev/null 2>&1 || true
if [ -n "$BASE" ]; then
  "$DOCKER" compose exec -T backend python /tmp/v2t/shotdiff.py $BASE $STAGE 2>&1 | grep -v Deprecation | grep -v getdata | sort -rn | head -40
  "$DOCKER" compose exec -T backend python /tmp/v2t/stylediff.py $BASE $STAGE 2>&1 | tail -1
  "$DOCKER" compose cp backend:/tmp/v2t/shots/diff_${BASE}_${STAGE} tests/artifacts/design/diff_${BASE}_${STAGE} >/dev/null 2>&1 || true
  "$DOCKER" compose cp backend:/tmp/v2t/shots/stylediff_${BASE}_${STAGE}.md tests/artifacts/design/stylediff_${BASE}_${STAGE}.md >/dev/null 2>&1 || true
fi
