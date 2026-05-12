#!/usr/bin/env bash
set -euo pipefail

# Download student PR heads into isolated local review branches.
# This does not checkout, merge, rebase, or modify the current working tree.

REPO_REMOTE="${1:-origin}"

fetch_pr() {
  local pr_number="$1"
  local branch_name="$2"
  local label="$3"

  echo "==> Fetching ${label}: PR #${pr_number} -> ${branch_name}"
  git fetch --no-tags "${REPO_REMOTE}" \
    "+pull/${pr_number}/head:refs/heads/${branch_name}"
}

fetch_pr 6 "codex/dgroup-pr6-review" "D group / Homework e api gui"
fetch_pr 7 "codex/bgroup-pr7-review" "B group / knowledge Graph RAG"
fetch_pr 8 "codex/agroup-pr8-review" "A group / harness evaluation"
fetch_pr 9 "codex/cgroup-pr9-review" "C group"

echo
echo "Downloaded review branches:"
git branch --list "codex/*group-pr*-review"
echo
echo "Current branch remains:"
git branch --show-current
