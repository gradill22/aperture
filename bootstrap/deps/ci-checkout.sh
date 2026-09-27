#!/bin/sh
# ci-checkout: fetch the commit under test from the local Gitea into the job's workspace.
# Replaces `uses: actions/checkout` (which would need a Node action fetched from somewhere).
# Env: CI_GIT_URL (http://gitea:3000), CI_TOKEN (the job token), GITHUB_REPOSITORY, GITHUB_SHA,
# GITHUB_REF (all but the first two are set by the runner).
set -eu
: "${CI_GIT_URL:?}" "${CI_TOKEN:?}" "${GITHUB_REPOSITORY:?}" "${GITHUB_SHA:?}"
auth="Authorization: Basic $(printf 'ci:%s' "$CI_TOKEN" | base64 | tr -d '\n')"
url="$CI_GIT_URL/$GITHUB_REPOSITORY.git"
git init -q .
git config --global --add safe.directory "$PWD"
# Fetch the exact commit; fall back to the ref if the server refuses a bare SHA.
git -c http.extraHeader="$auth" fetch -q --depth 1 "$url" "$GITHUB_SHA" \
  || git -c http.extraHeader="$auth" fetch -q --depth 50 "$url" "${GITHUB_REF:?}"
git checkout -q --detach "$GITHUB_SHA"
git log -1 --format='checked out %H: %s'
