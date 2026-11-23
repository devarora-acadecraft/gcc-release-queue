#!/usr/bin/env python3
"""Move developer671's open client PRs from one base branch to another (owner 2026-11-23: main -> master).

  python3 retarget.py report    what each repo and open PR would look like on the new base (changes nothing)
  python3 retarget.py apply     change the base of every open PR that gains no extra commits by the move

A PR is only moved when the new base already holds everything the old base has, so the PR still shows
exactly its own commits. Anything else is listed and left alone. The result is an issue in this repo.
"""
import os
import sys

import release as rq

OLD = os.environ.get('RETARGET_FROM', 'main')
NEW = os.environ.get('RETARGET_TO', 'master')


def compare(repo, base, head):
    try:
        c = rq.client_api(f'repos/{repo}/compare/{base}...{head}?per_page=1')
    except rq.Failure:
        return None  # GitHub answers 404 when the two have no common history
    return c['ahead_by'], c['behind_by']


def describe(repo, name):
    commits = rq.client_api(f'repos/{repo}/commits?sha={name}&per_page=3') or []
    out = [f"  - {c['sha'][:8]} {c['commit']['committer']['date']} {c['commit']['author']['name']}: "
           f"{c['commit']['message'].splitlines()[0][:80]}" for c in commits]
    return '\n'.join(out)


def branch_sha(repo, name):
    try:
        b = rq.client_api(f'repos/{repo}/branches/{name}')
    except rq.Failure:
        return None
    # GitHub answers a renamed branch's old name with the new branch: that is not the branch asked for.
    return b['commit']['sha'] if b.get('name') == name else None


def branch_names(repo):
    return [b['name'] for b in rq.client_api(f'repos/{repo}/branches?per_page=100') or []]


def main(mode):
    lines, moved, problems = [], 0, 0
    for name in rq.CLIENT_REPOS:
        repo = f'{rq.DEFAULT_OWNER}/{name}'
        info = rq.client_api(f'repos/{repo}')
        old_sha, new_sha = branch_sha(repo, OLD), branch_sha(repo, NEW)
        lines.append(f"\n### {repo}\ndefault branch `{info['default_branch']}`; `{OLD}` {(old_sha or 'missing')[:8]}; "
                     f"`{NEW}` {(new_sha or 'missing')[:8]}")
        names = branch_names(repo)
        lines.append(f'- {len(names)} branch(es); not ours: ' + ', '.join(
            f'`{n}`' for n in names if not n.startswith(('fix/', 'feat/', 'sec/', 'chore/')))[:900])
        if not new_sha:
            lines.append(f'- `{NEW}` does not exist: nothing moved')
            problems += 1
            continue
        lines.append(f'- latest on `{NEW}`:\n' + describe(repo, NEW))
        lines.append(f'- latest on `{OLD}`:\n' + describe(repo, OLD))
        cmp = compare(repo, OLD, NEW)
        if cmp is None:
            lines.append(f'- `{NEW}` and `{OLD}` share NO history: no PR can move as it is - nothing moved')
            problems += 1
            continue
        ahead, behind = cmp
        lines.append(f'- `{NEW}` vs `{OLD}`: {ahead} commit(s) only on `{NEW}`, {behind} commit(s) only on `{OLD}`')
        prs = rq.client_api(f'repos/{repo}/pulls?state=open&base={OLD}&per_page=50') or []
        prs = [p for p in prs if p['user']['login'] == rq.ID_NAME]
        if not prs:
            lines.append(f'- no open {rq.ID_NAME} PR on `{OLD}`')
        for p in prs:
            head = p['head']['sha']
            own = (compare(repo, OLD, head) or (None,))[0]
            on_new = (compare(repo, NEW, head) or (None,))[0]
            tag = f"#{p['number']} '{p['title']}' ({p['head']['ref']})"
            if on_new is None or on_new != own:
                lines.append(f'- {tag}: {own} commit(s) on `{OLD}` but {on_new} on `{NEW}` - NOT moved')
                problems += 1
                continue
            if mode == 'apply':
                rq.client_api(f"repos/{repo}/pulls/{p['number']}", 'PATCH', {'base': NEW})
                lines.append(f'- {tag}: moved to `{NEW}` ({own} commit(s))')
                moved += 1
            else:
                lines.append(f'- {tag}: can move to `{NEW}` unchanged ({own} commit(s))')
    head = f'Retarget {mode} {OLD} -> {NEW}: {moved} moved, {problems} need a decision' if mode == 'apply' \
        else f'Retarget report {OLD} -> {NEW}: {problems} need a decision'
    body = '\n'.join(lines)
    print(head + '\n' + body)
    rq.alert(f'{head} ({rq.now():%m-%d %H:%MZ})', body)
    return 1 if problems else 0


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'report'
    if mode not in ('report', 'apply') or not rq.CLIENT_TOKEN:
        print('usage: retarget.py report|apply (needs CLIENT_TOKEN)', file=sys.stderr)
        sys.exit(2)
    sys.exit(main(mode))
