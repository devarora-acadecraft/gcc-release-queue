#!/usr/bin/env bash
# Run in a gccplatform clone before EVERY push. Exits non-zero on any breach.
#   bash ~/.claude/skills/gccplatform-pr/precheck.sh [branch]   (default: HEAD)
set -u
ref=${1:-HEAD}
base=origin/main
fail=0
ok='developer671 317776374+developer671@users.noreply.github.com'

bad_people=$(git log --format='%an %ae|%cn %ce' "$base..$ref" | tr '|' '\n' | sort -u | grep -vx "$ok")
if [ -n "$bad_people" ]; then echo "FAIL author/committer not developer671:"; echo "$bad_people"; fail=1; fi

msgs=$(git log --format='%h %B' "$base..$ref" | grep -iE 'co-authored|claude|anthropic|generated with|\bprd\b|\buat\b')
if [ -n "$msgs" ]; then echo "FAIL commit messages contain forbidden text:"; echo "$msgs"; fail=1; fi

msg_tickets=$(git log --format='%h %s' "$base..$ref" | grep -iE 'ticket')
if [ -n "$msg_tickets" ]; then echo "CHECK 'ticket' in commit subjects (fine only if it means event tickets):"; echo "$msg_tickets"; fi

words=$(git diff "$base..$ref" | grep -E '^\+' | grep -iE 'claude|anthropic|\bprd\b|\buat\b')
if [ -n "$words" ]; then echo "FAIL added lines mention AI/PRD/UAT:"; echo "$words" | head -20; fail=1; fi

tickets=$(git diff "$base..$ref" | grep -E '^\+' | grep -iE 'ticket' | grep -viE 'event ticket|ticket(s|ed)? (sale|price|type|count|refund)|ticketType|ticket_|tickets?\b.*(event|registration)')
if [ -n "$tickets" ]; then echo "CHECK 'ticket' on added lines (fine only if it means event tickets):"; echo "$tickets" | head -10; fi

[ $fail -eq 0 ] && echo "precheck OK: $(git rev-list --count "$base..$ref") commits, developer671 only, no AI/PRD/UAT text"
exit $fail
