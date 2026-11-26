#!/usr/bin/env python3
"""Apply prepared client commits (a `git format-patch` file in patches/) onto the client's UAT branch and raise them
as a PR. For when the machine that prepared the commits cannot fetch the client repo (a bundle needs a base the
client has); the queue's CLIENT_TOKEN can.

  python3 patch.py <repo> patches/<file>.patch <branch> "<title>" report   apply + precheck, push nothing
  python3 patch.py <repo> patches/<file>.patch <branch> "<title>" apply    then push <branch> and open the PR
  ... apply new-pr                                                          its own PR even if one is open

When <branch> is the head of an open developer671 PR on the base, the patch is applied on top of that branch and
pushed to it (adding to the open PR, as release.py does), and the PR takes <title>. Otherwise a new PR is opened
from <branch> on the base.

A `.enc` patch (openssl enc -aes-256-cbc -pbkdf2 -iter 200000, key = PATCH_KEY secret) is decrypted first, and its
report names no files or commit subjects (this repo is public).

The commits keep the patch's author (developer671); the committer is developer671. Nothing is pushed when the patch
does not apply, the precheck fails, or a new PR is asked for while a developer671 PR is open on the base or
<branch> already exists. The result is an issue in this repo.
"""
import os
import subprocess
import sys
import tempfile

import release as rq

PATCH_KEY = os.environ.pop('PATCH_KEY', '')


def main(name, patch, branch, title, mode, new_pr=False):
    repo = f'{rq.DEFAULT_OWNER}/{name}'
    base = rq.BASE
    path = os.path.join(rq.ROOT, patch)
    if not os.path.isfile(path):
        raise rq.Failure(f'{patch} not found in the queue repo')
    quiet = patch.endswith('.enc')
    if quiet:
        if not PATCH_KEY:
            raise rq.Failure('PATCH_KEY secret is not set')
        plain = os.path.join(tempfile.mkdtemp(prefix='patch-in-'), 'patch.mbox')
        if subprocess.run(['openssl', 'enc', '-d', '-aes-256-cbc', '-pbkdf2', '-iter', '200000', '-pass', 'env:GCC_PATCH_KEY',
                           '-in', path, '-out', plain], env={'PATH': os.environ.get('PATH', ''), 'GCC_PATCH_KEY': PATCH_KEY},
                          capture_output=True).returncode:
            raise rq.Failure(f'{patch} could not be decrypted with PATCH_KEY')
        path = plain

    work = tempfile.mkdtemp(prefix='patch-')
    url = f'https://x-access-token:{rq.CLIENT_TOKEN}@github.com/{repo}.git'
    rq.run(['git', 'clone', '-q', '--filter=blob:none', '--branch', base, url, work], token=rq.CLIENT_TOKEN)
    rq.run(['git', 'config', 'user.name', rq.ID_NAME], cwd=work)
    rq.run(['git', 'config', 'user.email', rq.ID_EMAIL], cwd=work)

    open_prs = [p for p in rq.client_api(f'repos/{repo}/pulls?state=open&base={base}&per_page=50') or []
                if p['user']['login'] == rq.ID_NAME]
    target = None if new_pr else next((p for p in open_prs if p['head']['ref'] == branch), None)
    taken = bool(rq.run(['git', 'ls-remote', '--heads', 'origin', branch], cwd=work).stdout.strip())
    start = f'origin/{base}'
    if target:
        rq.run(['git', 'fetch', '-q', 'origin', f'+refs/heads/{branch}:refs/remotes/origin/{branch}'], cwd=work)
        start = f'origin/{branch}'
    rq.run(['git', 'checkout', '-q', '-B', 'work', start], cwd=work)
    tip = rq.run(['git', 'log', '-1', '--format=%h' if quiet else '--format=%h %s', start], cwd=work).stdout.strip()
    onto = f"#{target['number']} (`{branch}`)" if target else f'`{base}`'

    if rq.run(['git', 'am', '-q', '--3way', path], cwd=work, check=False).returncode:
        rq.run(['git', 'am', '--abort'], cwd=work, check=False)
        rq.alert(f'Patch blocked {repo} {branch} -> {base}',
                 f'`{patch}` does not apply on {onto} ({tip}). Nothing pushed.')
        return 1, None
    log = rq.run(['git', 'log', '--format=%h %an <%ae> | %cn <%ce> | %s', f'{start}..work'], cwd=work).stdout
    stat = rq.run(['git', 'diff', '--stat', f'{start}..work'], cwd=work).stdout
    ok, out = rq.precheck(work, 'work')
    report = (f'`{patch}` applied on {onto} ({tip}):\n```\n{log}{stat}```\n'
              f'precheck: {"OK" if ok else "FAILED"}\n```\n{out}\n```')
    if quiet:
        commits = len([line for line in log.splitlines() if line.strip()])
        files = stat.strip().splitlines()[-1].strip() if stat.strip() else 'no changes'
        report = (f'`{patch}` applied on {onto} ({tip}): {commits} commit(s); {files}\n'
                  f'precheck: {"OK" if ok else "FAILED (details withheld for an encrypted patch)"}')
    if not ok:
        rq.alert(f'Patch blocked by precheck {repo} {branch} -> {base}', f'Nothing pushed.\n\n{report}')
        return 1, None

    notes = []
    if not target and open_prs and not new_pr:
        notes.append('open developer671 PR on the base: ' + ', '.join(
            f"#{p['number']} (`{p['head']['ref']}`)" for p in open_prs) + '; name its branch to add to it')
    if not target and taken:
        notes.append(f'branch `{branch}` already exists on the client')
    if mode == 'report' or notes:
        msg = report + ('\n\nNot raised: ' + '; '.join(notes) if notes else '')
        print(msg)
        rq.alert(f'Patch {mode} {repo} {branch} -> {base} ({rq.now():%m-%d %H:%MZ})', msg)
        return (1 if notes else 0), None

    rq.run(['git', 'push', '-q', 'origin', f'work:refs/heads/{branch}'], cwd=work, token=rq.CLIENT_TOKEN)
    if target:
        if target['title'] != title:
            rq.client_api(f"repos/{repo}/pulls/{target['number']}", 'PATCH', {'title': title})
        pr = target
    else:
        pr = rq.client_api(f'repos/{repo}/pulls', 'POST', {'title': title, 'head': branch, 'base': base, 'body': ''})
    msg = f"{pr['html_url']}\n\n{report}"
    print(msg)
    shown = (f"Released: {repo} PR #{pr['number']} ({base})" if quiet
             else f"Released: {repo} PR #{pr['number']} '{title}' ({base})")
    rq.alert(shown, msg, close=True)
    return 0, pr['html_url']


if __name__ == '__main__':
    if len(sys.argv) not in (6, 7) or sys.argv[5] not in ('report', 'apply') or not rq.CLIENT_TOKEN             or sys.argv[6:] not in ([], ['new-pr'], ['']):
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    try:
        sys.exit(main(*sys.argv[1:6], new_pr=sys.argv[6:] == ['new-pr'])[0])
    except rq.Failure as exc:
        rq.alert(f'Patch error {sys.argv[1]} {sys.argv[3]}', f'```\n{rq.redact(str(exc))}\n```')
        raise
