#!/usr/bin/env python3
"""Release due queue entries as PRs on the client repos.

Runs on a GitHub-hosted runner (see .github/workflows). Safe to run any number of
times: an entry with a state file is finished, and a client PR that already exists
for an entry is recorded instead of raised again.

  python3 release.py            release every due entry
  python3 release.py --health   check the client token and repo access
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
CLIENT_TOKEN = os.environ.get('CLIENT_TOKEN', '')
QUEUE_TOKEN = os.environ.get('GH_TOKEN', '')
QUEUE_REPO = os.environ.get('GITHUB_REPOSITORY', 'devarora-acadecraft/gcc-release-queue')
ALERT_USER = os.environ.get('ALERT_USER', 'devarora-acadecraft')
DEFAULT_OWNER = 'gccplatform'
CLIENT_REPOS = ['gcc-api', 'gcc-web', 'gcc-super-admin']
ID_NAME = 'developer671'
ID_EMAIL = '317776374+developer671@users.noreply.github.com'
OVERDUE = dt.timedelta(minutes=45)
EXPIRY_WARNING = dt.timedelta(days=7)
FINAL = ('released', 'blocked', 'empty')


class Failure(Exception):
    pass


def now():
    return dt.datetime.now(dt.timezone.utc)


def redact(text):
    return text.replace(CLIENT_TOKEN, '***') if CLIENT_TOKEN else text


def run(cmd, cwd=None, token=None, check=True):
    env = dict(os.environ)
    env['GH_TOKEN'] = token if token is not None else QUEUE_TOKEN
    env['GIT_TERMINAL_PROMPT'] = '0'
    if cwd is None and token == CLIENT_TOKEN:
        cwd = tempfile.gettempdir()  # outside the checkout, whose extraheader would replace the client token
    p = subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True)
    if check and p.returncode:
        raise Failure(redact(f"`{' '.join(cmd[:4])}` failed: {(p.stderr or p.stdout).strip()[-1500:]}"))
    return p


def client_api(path, method='GET', fields=None):
    cmd = ['gh', 'api', '-X', method, path]
    for k, v in (fields or {}).items():
        cmd += ['-f', f'{k}={v}']
    out = run(cmd, token=CLIENT_TOKEN).stdout
    return json.loads(out) if out.strip() else None


# --- alerts: issues in the queue repo, which GitHub emails to ALERT_USER ---

def alert(title, body, close=False):
    title = title[:240]
    found = run(['gh', 'issue', 'list', '-R', QUEUE_REPO, '--state', 'open', '--limit', '100',
                 '--json', 'title'], check=False)
    if found.returncode == 0 and any(i['title'] == title for i in json.loads(found.stdout or '[]')):
        return
    made = run(['gh', 'issue', 'create', '-R', QUEUE_REPO, '--title', title,
                '--body', f'@{ALERT_USER} {redact(body)}'], check=False)
    if made.returncode:
        print(f'ALERT NOT SENT: {title}: {made.stderr}', file=sys.stderr)
        return
    if close:
        run(['gh', 'issue', 'close', made.stdout.strip(), '-R', QUEUE_REPO], check=False)


# --- queue + state ---

def load_queue():
    with open(os.path.join(ROOT, 'queue.json'), encoding='utf-8') as f:
        entries = json.load(f)['entries']
    for e in entries:
        path = os.path.join(ROOT, 'state', f"{e['id']}.json")
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                e['state'] = json.load(f)
    return entries


def save_state(e, status, pr_url=None, note=''):
    os.makedirs(os.path.join(ROOT, 'state'), exist_ok=True)
    e['state'] = {'status': status, 'pr': pr_url, 'note': note, 'at': now().isoformat(timespec='seconds')}
    with open(os.path.join(ROOT, 'state', f"{e['id']}.json"), 'w', encoding='utf-8') as f:
        json.dump(e['state'], f, indent=2)
        f.write('\n')


def commit_state():
    run(['git', 'add', 'state'], cwd=ROOT)
    if run(['git', 'diff', '--cached', '--quiet'], cwd=ROOT, check=False).returncode == 0:
        return
    run(['git', '-c', 'user.name=github-actions[bot]',
         '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
         'commit', '-q', '-m', 'Record release state'], cwd=ROOT)
    for _ in range(5):
        if run(['git', 'push', '-q', 'origin', 'HEAD:main'], cwd=ROOT, check=False).returncode == 0:
            return
        run(['git', 'pull', '-q', '--rebase', 'origin', 'main'], cwd=ROOT, check=False)
    raise Failure('could not push release state to the queue repo')


# --- titles ---

def merge_titles(current, extra):
    def parts(title):
        out = {}
        for seg in title.split(';'):
            head, _, items = seg.partition(':')
            lst = out.setdefault(head.strip(), [])
            lst += [i.strip() for i in items.split(',') if i.strip() and i.strip() not in lst]
        return out

    merged = parts(current)
    for head, items in parts(extra).items():
        lst = merged.setdefault(head, [])
        lst += [i for i in items if i not in lst]
    segs = []
    for head, items in merged.items():
        if items and all(re.fullmatch(r'#\d+', i) for i in items):
            items = sorted(items, key=lambda i: int(i[1:]))
        segs.append(f"{head}: {', '.join(items)}" if items else head)
    return '; '.join(segs)


# --- one entry ---

def full_name(e):
    return e['repo'] if '/' in e['repo'] else f"{DEFAULT_OWNER}/{e['repo']}"


def precheck(work, ref):
    p = run(['bash', os.path.join(ROOT, 'precheck.sh'), ref], cwd=work, check=False)
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def release(e):
    repo = full_name(e)
    branch = e['branch']

    existing = client_api(f'repos/{repo}/pulls?state=all&head={repo.split("/")[0]}:{branch}&per_page=5')
    if existing:
        save_state(e, 'released', existing[0]['html_url'], 'PR already existed')
        return

    work = tempfile.mkdtemp(prefix='rq-')
    url = f'https://x-access-token:{CLIENT_TOKEN}@github.com/{repo}.git'
    run(['git', 'clone', '-q', '--filter=blob:none', url, work], token=CLIENT_TOKEN)
    run(['git', 'config', 'user.name', ID_NAME], cwd=work)
    run(['git', 'config', 'user.email', ID_EMAIL], cwd=work)
    bundle = os.path.join(ROOT, e['bundle'])
    run(['git', 'bundle', 'verify', '-q', bundle], cwd=work)
    run(['git', 'fetch', '-q', bundle, f"+{e['bundle_ref']}:refs/queue/prepared"], cwd=work)

    notes = []
    run(['git', 'checkout', '-q', '-B', 'work', 'refs/queue/prepared'], cwd=work)
    if run(['git', 'rebase', '-q', 'origin/main'], cwd=work, check=False).returncode:
        run(['git', 'rebase', '--abort'], cwd=work, check=False)
        run(['git', 'checkout', '-q', '-B', 'work', 'refs/queue/prepared'], cwd=work)
        notes.append('client main moved and these commits no longer rebase cleanly; '
                     'pushed as prepared, so the PR will show a merge conflict')

    if run(['git', 'rev-list', '--count', 'origin/main..work'], cwd=work).stdout.strip() == '0':
        save_state(e, 'empty', None, 'client main already contains these changes')
        alert(f"Nothing to release: {e['id']}", f"`{repo}` main already contains `{branch}`. No PR raised.")
        return

    ok, out = precheck(work, 'work')
    if not ok:
        save_state(e, 'blocked', None, out)
        alert(f"Blocked by precheck: {e['id']}", f"Nothing was pushed to `{repo}`.\n\n```\n{out}\n```")
        return

    open_prs = client_api(f'repos/{repo}/pulls?state=open&base=main&per_page=50') or []
    open_prs = sorted((p for p in open_prs if p['user']['login'] == ID_NAME),
                      key=lambda p: p['created_at'], reverse=True)
    if open_prs and not e.get('new_pr'):
        pr = open_prs[0]
        head = pr['head']['ref']
        run(['git', 'fetch', '-q', 'origin', f'+refs/heads/{head}:refs/remotes/origin/pr'], cwd=work)
        cherry = run(['git', 'cherry', 'refs/remotes/origin/pr', 'work'], cwd=work).stdout.split('\n')
        if all(line.startswith('-') for line in cherry if line):
            save_state(e, 'released', pr['html_url'], 'already in the open PR')
            return
        run(['git', 'checkout', '-q', '-B', 'append', 'refs/remotes/origin/pr'], cwd=work)
        commits = run(['git', 'rev-list', '--reverse', 'origin/main..work'], cwd=work).stdout.split()
        picked = True
        for c in commits:
            if run(['git', 'cherry-pick', c], cwd=work, check=False).returncode:
                run(['git', 'cherry-pick', '--abort'], cwd=work, check=False)
                picked = False
                break
        if picked:
            ok, out = precheck(work, 'append')
            if not ok:
                save_state(e, 'blocked', None, out)
                alert(f"Blocked by precheck: {e['id']}",
                      f"Adding to open PR {pr['html_url']} failed precheck. Nothing pushed.\n\n```\n{out}\n```")
                return
            run(['git', 'push', '-q', 'origin', f'append:refs/heads/{head}'], cwd=work, token=CLIENT_TOKEN)
            title = merge_titles(pr['title'], e['title'])
            if title != pr['title']:
                client_api(f"repos/{repo}/pulls/{pr['number']}", 'PATCH', {'title': title})
            save_state(e, 'released', pr['html_url'], f"added to open PR; title now: {title}")
            alert(f"Released: {repo} PR #{pr['number']} '{title}'",
                  f"Added `{branch}` to the open PR {pr['html_url']}.", close=True)
            return
        notes.append(f"could not add these commits to open PR #{pr['number']} without a conflict, "
                     'so a separate PR was raised')

    target = branch
    remote = run(['git', 'ls-remote', '--heads', 'origin', branch], cwd=work).stdout.split()
    if remote and remote[0] != run(['git', 'rev-parse', 'work'], cwd=work).stdout.strip():
        target = f'{branch}-{now():%m%d%H%M}'
        notes.append(f'branch `{branch}` already existed on the client with other commits; used `{target}`')
    run(['git', 'push', '-q', 'origin', f'work:refs/heads/{target}'], cwd=work, token=CLIENT_TOKEN)
    pr = client_api(f'repos/{repo}/pulls', 'POST', {'title': e['title'], 'head': target, 'base': 'main', 'body': ''})
    save_state(e, 'released', pr['html_url'], '; '.join(notes))
    alert(f"Released: {repo} PR #{pr['number']} '{e['title']}'",
          pr['html_url'] + ''.join(f'\n\n**Check:** {n}' for n in notes), close=not notes)


# --- entry points ---

def release_due():
    entries = load_queue()
    pending = [e for e in entries if e.get('state', {}).get('status') not in FINAL]
    due = [e for e in pending if dt.datetime.fromisoformat(e['release_at']) <= now()]
    due.sort(key=lambda e: (e['repo'].split('/')[-1] != 'gcc-api', e['release_at'], e['id']))
    print(f'{len(entries)} entries, {len(pending)} pending, {len(due)} due')

    failed = False
    for e in due:
        print(f"releasing {e['id']} -> {full_name(e)}")
        try:
            release(e)
            print(f"  {e['state']['status']}: {e['state'].get('pr') or ''} {e['state'].get('note') or ''}")
        except Exception as exc:  # keep going; the entry stays pending and is retried
            failed = True
            print(f'  error: {redact(str(exc))}', file=sys.stderr)
            alert(f"Release error: {e['id']}",
                  f"Will retry on the next trigger.\n\n```\n{redact(str(exc))}\n```")
        commit_state()

    for e in pending:
        if 'state' not in e and dt.datetime.fromisoformat(e['release_at']) + OVERDUE <= now():
            alert(f"Overdue: {e['id']}",
                  f"`{e['title']}` for `{full_name(e)}` was due at {e['release_at']} and is still not released.")
    return 1 if failed else 0


def health():
    problems = []
    req = urllib.request.Request('https://api.github.com/user', headers={'Authorization': f'token {CLIENT_TOKEN}'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            login = json.load(r).get('login')
            expiry = r.headers.get('github-authentication-token-expiration')
        if login != ID_NAME:
            problems.append(f'token belongs to `{login}`, not `{ID_NAME}`')
        if expiry:
            when = dt.datetime.strptime(expiry.replace(' UTC', ' +0000'), '%Y-%m-%d %H:%M:%S %z')
            if when - now() <= EXPIRY_WARNING:
                problems.append(f'token expires {expiry}')
    except urllib.error.HTTPError as exc:
        problems.append(f'token rejected by GitHub (HTTP {exc.code})')
    except Exception as exc:
        problems.append(f'could not reach GitHub: {exc}')

    if not problems:
        for name in CLIENT_REPOS:
            try:
                info = client_api(f'repos/{DEFAULT_OWNER}/{name}')
                if not info['permissions']['push']:
                    problems.append(f'no push access to {DEFAULT_OWNER}/{name}')
                run(['git', 'ls-remote', '-q', '--heads',
                     f'https://x-access-token:{CLIENT_TOKEN}@github.com/{DEFAULT_OWNER}/{name}.git', 'main'],
                    token=CLIENT_TOKEN)
            except Exception as exc:
                problems.append(f'{DEFAULT_OWNER}/{name}: {redact(str(exc))}')

    if problems:
        alert('Client token needs attention', 'Releases will fail until this is fixed:\n\n- ' + '\n- '.join(problems))
        print('\n'.join(problems), file=sys.stderr)
        return 1
    print('token OK, push access to ' + ', '.join(CLIENT_REPOS))
    return 0


if __name__ == '__main__':
    if not CLIENT_TOKEN:
        alert('Client token missing', 'The CLIENT_TOKEN secret is not set, so nothing can be released.')
        sys.exit(1)
    sys.exit(health() if '--health' in sys.argv else release_due())
