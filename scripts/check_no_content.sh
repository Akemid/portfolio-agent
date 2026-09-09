#!/usr/bin/env bash
# Fails the build if a diff introduces any path under `content/` or any PDF
# file (case-insensitive), the two shapes personal content can take.
#
# Usage: check_no_content.sh <diff-file>
#   <diff-file>: path to a unified diff (defaults to `git diff` output piped
#   in by the CI step that produces the pull request diff).
#
# See `knowledge-base` spec, *Content Never Committed* requirement,
# scenario *CI content check*.
set -euo pipefail

diff_file="${1:?usage: check_no_content.sh <diff-file>}"

if grep -qE '^\+\+\+ b/(content/|.*/content/)' "$diff_file"; then
  echo "ERROR: diff adds a path under content/ — personal content must never be committed." >&2
  exit 1
fi

if grep -qiE '^\+\+\+ b/.*\.pdf$' "$diff_file"; then
  echo "ERROR: diff adds a PDF file — personal documents must never be committed." >&2
  exit 1
fi

exit 0
