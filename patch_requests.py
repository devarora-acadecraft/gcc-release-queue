#!/usr/bin/env python3
"""Run the requests in patches/requests.json that have no state yet. Triggered by a push of that file, so a machine
that can push to this repo (acadeserver, over its SSH key) but cannot dispatch workflows can raise client PRs.

  {"requests": [{"id": "20261126-api-1", "repo": "gcc-api", "patch": "patches/x.patch.enc", "branch": "fix/x",
                 "title": "Security fixes: ...", "mode": "report" | "apply", "new_pr": true}]}

Each request runs patch.main once; the result is state/patch-<id>.json (report-ok, report-failed, raised, blocked, error)
plus an issue. To run a request again, use a new id.
"""
import json
import os
import sys

import patch as pt
import release as rq


def main():
    path = os.path.join(rq.ROOT, 'patches', 'requests.json')
    if not os.path.exists(path):
        print('no patch requests')
        return 0
    with open(path, encoding='utf-8') as f:
        requests = json.load(f).get('requests', [])
    rc = 0
    for r in requests:
        entry = {'id': f"patch-{r['id']}"}
        if os.path.exists(os.path.join(rq.ROOT, 'state', f"{entry['id']}.json")):
            continue
        try:
            code, url = pt.main(r['repo'], r['patch'], r['branch'], r['title'], r['mode'], bool(r.get('new_pr')))
            if code == 0:
                status = 'raised' if url else 'report-ok'
            else:
                status = 'blocked' if r['mode'] == 'apply' else 'report-failed'
            rq.save_state(entry, status, url)
        except rq.Failure as exc:
            rq.alert(f"Patch error {r['repo']} {r['id']}", f'```\n{rq.redact(str(exc))}\n```')
            rq.save_state(entry, 'error', None, rq.redact(str(exc))[:300])
            rc = 1
    rq.commit_state()
    return rc


if __name__ == '__main__':
    if not rq.CLIENT_TOKEN:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main())
