# gcc-release-queue

Raises prepared PRs on the client repos (`gccplatform/gcc-api`, `gcc-web`, `gcc-super-admin`)
at a set time instead of straight away. Default slot: **10:30 IST daily**.

## Queue a branch

In a client clone, with `origin/main` freshly fetched and the branch passing `precheck.sh`:

```
bash enqueue.sh <client-clone> <branch> "Member fixes: #8, #16" [2026-10-28T10:30:00+05:30]
```

This saves the branch as a bundle in `bundles/` and adds an entry to `queue.json`.
Bundles keep the client's workflow files inert here.

## What a release does

`release.py`, on a GitHub-hosted runner, for every due entry without a `state/<id>.json`
(gcc-api first):

1. If the client already has a PR for the branch, it records that PR and stops.
2. Rebases the branch onto client `main`. On a conflict, it pushes the branch as prepared and flags it.
3. Runs `precheck.sh`. If that fails, it pushes nothing and marks the entry `blocked`.
4. If developer671 has an open PR in that repo, it adds the commits to it and merges the
   `#n` title. Otherwise it opens a new PR with an empty body.
5. Writes `state/<id>.json`. A `released`, `blocked` or `empty` state is final. To retry
   an entry, delete its state file.

## Triggers

- **acadeserver**: a systemd user timer (`tick/`) with `Persistent=true` checks every 10 min.
  When something is due, it pushes to the `tick` branch using a deploy key for this repo only.
- **GitHub schedule** (backup): every 10 min from 10:37 to 12:27 IST, then every 2 h.

Duplicate runs do nothing.

## Alerts

Alerts are issues in this repo, mentioning `@devarora-acadecraft`:
`Released` (closed at once), `Release error` (retried), `Blocked by precheck`,
`Nothing to release`, `Overdue` (more than 45 min late), and `Client token needs attention`
(daily `health` check).

## Secrets

`CLIENT_TOKEN` is a developer671 token with push access to the three client repos. To replace it:

```
gh secret set CLIENT_TOKEN -R Crux-Sphere/gcc-release-queue
```
