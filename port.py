#!/usr/bin/env python3
"""Bring a developer671 PR that was merged into the client's main over to their UAT branch as a new PR.
(owner 2026-11-23: PRs target Uat_Master; #67, #68 and #53 had already been merged into main.)

  python3 port.py <repo> <pr number> "<title>" report    which of the PR's commits the UAT branch lacks
  python3 port.py <repo> <pr number> "<title>" apply     cherry-pick those onto it, precheck, push, open the PR

Only the PR's own (non-merge) commits are picked; commits the UAT branch already has (same patch) are skipped.
A conflict or a failed precheck pushes nothing. The result is an issue in this repo.
"""
import os
import sys
import tempfile

import release as rq


def main(name, number, title, mode):
    repo = f'{rq.DEFAULT_OWNER}/{name}'
    base = rq.BASE
    pr = rq.client_api(f'repos/{repo}/pulls/{number}')
    if pr['user']['login'] != rq.ID_NAME:
        raise rq.Failure(f'#{number} is not a {rq.ID_NAME} PR')
    commits = [c['sha'] for c in rq.client_api(f'repos/{repo}/pulls/{number}/commits?per_page=100') or []
               if len(c['parents']) == 1]

    work = tempfile.mkdtemp(prefix='port-')
    url = f'https://x-access-token:{rq.CLIENT_TOKEN}@github.com/{repo}.git'
    rq.run(['git', 'clone', '-q', '--filter=blob:none', url, work], token=rq.CLIENT_TOKEN)
    rq.run(['git', 'config', 'user.name', rq.ID_NAME], cwd=work)
    rq.run(['git', 'config', 'user.email', rq.ID_EMAIL], cwd=work)
    rq.run(['git', 'fetch', '-q', 'origin', f'+refs/pull/{number}/head:refs/remotes/origin/pr'], cwd=work)

    missing = []
    for sha in commits:
        line = rq.run(['git', 'cherry', f'origin/{base}', sha, f'{sha}^'], cwd=work).stdout.strip()
        if line.startswith('+'):
            missing.append(sha)
    subject = lambda sha: rq.run(['git', 'log', '-1', '--format=%h %s', sha], cwd=work).stdout.strip()
    lines = [f"#{number} '{pr['title']}' ({'merged ' + pr['merged_at'] if pr.get('merged_at') else pr['state']} "
             f"into `{pr['base']['ref']}`): {len(commits)} commit(s), {len(missing)} missing from `{base}`"]
    lines += [f'- {subject(sha)}' for sha in missing]
    if not missing or mode == 'report':
        print('\n'.join(lines))
        rq.alert(f'Port {mode} {repo} #{number} -> {base} ({rq.now():%m-%d %H:%MZ})', '\n'.join(lines))
        return 0

    rq.run(['git', 'checkout', '-q', '-B', 'port', f'origin/{base}'], cwd=work)
    for sha in missing:
        if rq.run(['git', 'cherry-pick', sha], cwd=work, check=False).returncode:
            rq.run(['git', 'cherry-pick', '--abort'], cwd=work, check=False)
            rq.alert(f'Port blocked {repo} #{number} -> {base}',
                     f'`{subject(sha)}` does not apply cleanly on `{base}`. Nothing pushed.\n\n' + '\n'.join(lines))
            return 1
    ok, out = rq.precheck(work, 'port')
    if not ok:
        rq.alert(f'Port blocked by precheck {repo} #{number} -> {base}', f'Nothing pushed.\n\n```\n{out}\n```')
        return 1

    target = f"{pr['head']['ref']}-uat"
    if rq.run(['git', 'ls-remote', '--heads', 'origin', target], cwd=work).stdout.strip():
        target = f'{target}-{rq.now():%m%d%H%M}'
    rq.run(['git', 'push', '-q', 'origin', f'port:refs/heads/{target}'], cwd=work, token=rq.CLIENT_TOKEN)
    new = rq.client_api(f'repos/{repo}/pulls', 'POST', {'title': title, 'head': target, 'base': base, 'body': ''})
    msg = f"{new['html_url']}\n\nPorted from #{number} onto `{base}`:\n" + '\n'.join(lines[1:])
    print(msg)
    rq.alert(f"Released: {repo} PR #{new['number']} '{title}' ({base})", msg, close=True)
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 5 or sys.argv[4] not in ('report', 'apply') or not rq.CLIENT_TOKEN:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    try:
        sys.exit(main(sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]))
    except rq.Failure as exc:
        rq.alert(f'Port error {sys.argv[1]} #{sys.argv[2]}', f'```\n{rq.redact(str(exc))}\n```')
        raise
