#!/usr/bin/env bash
# Queue a prepared client branch for the next release slot (10:30 IST) or a given time.
#   bash enqueue.sh <client-clone> <branch> "<PR title>" [release_at, ISO 8601 with offset]
# The clone's origin/main must be freshly fetched and the branch must pass precheck.
set -euo pipefail
clone=$1 branch=$2 title=$3 at=${4:-}
q=$(cd "$(dirname "$0")" && pwd)

repo=$(git -C "$clone" remote get-url origin | sed -E 's#^.*github\.com[:/]##; s#\.git$##')
case "$repo" in */*) ;; *) echo "cannot read owner/repo from origin: $repo" >&2; exit 1 ;; esac

(cd "$clone" && bash "$q/precheck.sh" "$branch")

if [ -z "$at" ]; then
  at=$(python -c "
import datetime as dt
ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
now = dt.datetime.now(ist)
slot = now.replace(hour=10, minute=30, second=0, microsecond=0)
print((slot if slot > now else slot + dt.timedelta(days=1)).isoformat())")
fi

id="$(date -u +%Y%m%d-%H%M%S)-${repo##*/}-$(echo "$branch" | tr '/' '-')"
git -C "$q" pull -q --rebase origin main
git -C "$clone" bundle create "$q/bundles/$id.bundle" "origin/main..$branch" 2>/dev/null

python - "$q/queue.json" "$id" "$repo" "$branch" "$title" "$at" <<'PY'
import json, sys
path, id_, repo, branch, title, at = sys.argv[1:]
with open(path, encoding='utf-8') as f:
    data = json.load(f)
data['entries'].append({'id': id_, 'repo': repo, 'branch': branch, 'title': title, 'release_at': at,
                        'bundle': f'bundles/{id_}.bundle', 'bundle_ref': f'refs/heads/{branch}'})
with open(path, 'w', encoding='utf-8', newline='\n') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
PY

git -C "$q" add queue.json "bundles/$id.bundle"
git -C "$q" commit -q -m "Queue $repo $branch for $at"
git -C "$q" push -q origin HEAD:main
echo "queued $id -> $repo '$title' at $at"
