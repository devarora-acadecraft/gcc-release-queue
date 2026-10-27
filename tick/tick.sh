#!/usr/bin/env bash
# Runs every 10 min from a systemd user timer on acadeserver. When a queue entry is
# due and not yet released, it pushes a commit to the `tick` branch, which starts the
# release workflow on GitHub. Uses a deploy key that only works on this repo.
set -euo pipefail
dir="$HOME/gcc-release-tick/repo.git"
export GIT_SSH_COMMAND="ssh -i $HOME/.ssh/gcc_release_queue -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20"
[ -d "$dir" ] || git clone -q --bare --filter=blob:none git@github.com:devarora-acadecraft/gcc-release-queue.git "$dir"
cd "$dir"
git fetch -q origin '+refs/heads/*:refs/remotes/origin/*'

due=$(python3 - <<'PY'
import datetime as dt, json, subprocess
def git(*a): return subprocess.run(['git', *a], capture_output=True, text=True).stdout
queue = json.loads(git('show', 'origin/main:queue.json') or '{"entries": []}')
done = set(git('ls-tree', '--name-only', 'origin/main', 'state/').split())
now = dt.datetime.now(dt.timezone.utc)
print(' '.join(e['id'] for e in queue['entries']
               if f"state/{e['id']}.json" not in done and dt.datetime.fromisoformat(e['release_at']) <= now))
PY
)
[ -z "$due" ] && { echo "nothing due"; exit 0; }

sha=$(git -c user.name=release-tick -c user.email=release-tick@acadeserver \
  commit-tree 'refs/remotes/origin/main^{tree}' -p refs/remotes/origin/main -m "tick $(date -u +%FT%TZ): $due")
git push -q -f origin "$sha:refs/heads/tick"
echo "ticked for: $due"
