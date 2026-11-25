#!/usr/bin/env python3
"""Apply one prepared client commit (a `git format-patch` file in patches/) onto the client's UAT branch and raise it
as a new PR. For when the machine that prepared the commit cannot fetch the client repo (a bundle needs a base the
client has); the queue's CLIENT_TOKEN can.

  python3 patch.py <repo> patches/<file>.patch <branch> "<title>" report   apply + precheck, push nothing
  python3 patch.py <repo> patches/<file>.patch <branch> "<title>" apply    then push <branch> and open the PR

The commit keeps the patch's author (developer671); the committer is developer671. Nothing is pushed when the patch
does not apply, the precheck fails, the branch already exists, or a developer671 PR is open on the base (adding to an
open PR is release.py's job). The result is an issue in this repo.
"""
import os
import sys
import tempfile

import release as rq


def main(name, patch, branch, title, mode):
    repo = f'{rq.DEFAULT_OWNER}/{name}'
    base = rq.BASE
    path = os.path.join(rq.ROOT, patch)
    if not os.path.isfile(path):
        raise rq.Failure(f'{patch} not found in the queue repo')

    work = tempfile.mkdtemp(prefix='patch-')
    url = f'https://x-access-token:{rq.CLIENT_TOKEN}@github.com/{repo}.git'
    rq.run(['git', 'clone', '-q', '--filter=blob:none', '--branch', base, url, work], token=rq.CLIENT_TOKEN)
    rq.run(['git', 'config', 'user.name', rq.ID_NAME], cwd=work)
    rq.run(['git', 'config', 'user.email', rq.ID_EMAIL], cwd=work)
    rq.run(['git', 'checkout', '-q', '-B', 'work', f'origin/{base}'], cwd=work)
    tip = rq.run(['git', 'log', '-1', '--format=%h %s', f'origin/{base}'], cwd=work).stdout.strip()

    if rq.run(['git', 'am', '-q', '--3way', path], cwd=work, check=False).returncode:
        rq.run(['git', 'am', '--abort'], cwd=work, check=False)
        rq.alert(f'Patch blocked {repo} {branch} -> {base}',
                 f'`{patch}` does not apply on `{base}` ({tip}). Nothing pushed.')
        return 1
    log = rq.run(['git', 'log', '--format=%h %an <%ae> | %cn <%ce> | %s', f'origin/{base}..work'], cwd=work).stdout
    stat = rq.run(['git', 'diff', '--stat', f'origin/{base}..work'], cwd=work).stdout
    ok, out = rq.precheck(work, 'work')
    report = (f'`{patch}` applied on `{base}` ({tip}):\n```\n{log}{stat}```\n'
              f'precheck: {"OK" if ok else "FAILED"}\n```\n{out}\n```')
    if not ok:
        rq.alert(f'Patch blocked by precheck {repo} {branch} -> {base}', f'Nothing pushed.\n\n{report}')
        return 1

    open_prs = [p for p in rq.client_api(f'repos/{repo}/pulls?state=open&base={base}&per_page=50') or []
                if p['user']['login'] == rq.ID_NAME]
    taken = bool(rq.run(['git', 'ls-remote', '--heads', 'origin', branch], cwd=work).stdout.strip())
    notes = []
    if open_prs:
        notes.append('open developer671 PR on the base: ' + ', '.join(f"#{p['number']}" for p in open_prs))
    if taken:
        notes.append(f'branch `{branch}` already exists on the client')
    if mode == 'report' or notes:
        msg = report + ('\n\nNot raised: ' + '; '.join(notes) if notes else '')
        print(msg)
        rq.alert(f'Patch {mode} {repo} {branch} -> {base} ({rq.now():%m-%d %H:%MZ})', msg)
        return 1 if notes else 0

    rq.run(['git', 'push', '-q', 'origin', f'work:refs/heads/{branch}'], cwd=work, token=rq.CLIENT_TOKEN)
    new = rq.client_api(f'repos/{repo}/pulls', 'POST', {'title': title, 'head': branch, 'base': base, 'body': ''})
    msg = f"{new['html_url']}\n\n{report}"
    print(msg)
    rq.alert(f"Released: {repo} PR #{new['number']} '{title}' ({base})", msg, close=True)
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 6 or sys.argv[5] not in ('report', 'apply') or not rq.CLIENT_TOKEN:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    try:
        sys.exit(main(*sys.argv[1:]))
    except rq.Failure as exc:
        rq.alert(f'Patch error {sys.argv[1]} {sys.argv[3]}', f'```\n{rq.redact(str(exc))}\n```')
        raise
