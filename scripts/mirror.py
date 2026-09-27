"""Mirror a CI-green commit from Gitea (canonical) to GitHub, from this machine.

    uv run python scripts/mirror.py [<ref>] [--dry-run]     (default ref: gitea/main)

Gitea has no route to the internet (ci/docker-compose.yml), so it cannot push-mirror; this is the
one-way host-side mirror instead. It pushes to the `github` remote only when:
  - the commit is on Gitea (`gitea/<branch>` after a fetch),
  - its newest push-triggered Actions run on Gitea completed with success, and
  - the push is a fast-forward of github/main (git refuses anything else; nothing is forced).
It uses your existing GitHub credential (Git Credential Manager). Dev tooling, never part of the
running system: like data/snapshot/ and bootstrap/, it is networked by design.
"""

import subprocess
import sys

import ci

BRANCH = "main"


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry_run = "--dry-run" in sys.argv
    repo = ci.gitea_repo()
    if not repo:
        print("no usable `gitea` remote; see ci/README.md")
        return 1
    if subprocess.run(["git", "fetch", "-q", "gitea", BRANCH], cwd=ci.ROOT, check=False).returncode:
        print("could not fetch from Gitea (is the CI stack up? uv run python scripts/ci.py up)")
        return 1
    ref = args[0] if args else f"gitea/{BRANCH}"
    sha = ci.git_out("rev-parse", "--verify", f"{ref}^{{commit}}")
    if not sha:
        print(f"unknown ref {ref!r}")
        return 1
    if subprocess.run(["git", "merge-base", "--is-ancestor", sha, f"gitea/{BRANCH}"],
                      cwd=ci.ROOT, check=False).returncode:  # fmt: skip
        print(f"{sha[:12]} is not on gitea/{BRANCH}: push it to Gitea (and let CI run) first")
        return 1
    status, run = ci.latest_run(repo, sha)
    if status != 200:
        print(f"Gitea actions API: HTTP {status} (private repo? GITEA_READ_TOKEN in ci/.env)")
        return 1
    state = f"{run.get('status')}/{run.get('conclusion')}" if run else "no run"
    print(f"{repo}@{sha[:12]}: CI {state}")
    if not run or run.get("status") != "completed" or run.get("conclusion") != "success":
        print("not mirroring: only commits with a green Gitea run go to GitHub")
        return 1
    cmd = ["git", "push", *(["--dry-run"] if dry_run else []), "github", f"{sha}:refs/heads/{BRANCH}"]  # fmt: skip
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ci.ROOT, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
