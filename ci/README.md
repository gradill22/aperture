# Local CI (Phase 5)

Gitea 1.27 + act_runner 0.6, fully offline. Gitea, the runner and every job container live on the
internal Docker network `aperture-ci` (no route out); `gitea-edge` publishes the UI and git on
`127.0.0.1:3000` only. Workflow: [`.gitea/workflows/ci.yml`](../.gitea/workflows/ci.yml).

```
uv run python scripts/ci.py up      # seed the build job's data volume, start Gitea (+ runner once registered)
uv run python scripts/ci.py down    # stop; Gitea's repos, users and the runner registration are kept
uv run python scripts/verify.py phase5
```

## One-time setup (done by you: accounts, tokens and credentials never go through the agent)

1. `uv run python scripts/ci.py up`, then open <http://localhost:3000>.
2. **Install page**: keep SQLite and the prefilled settings, create the administrator account
   under "Administrator Account Settings", and click Install.
3. **Repository**: + → New Repository, name `aperture`, no template, not initialised.
   - Public (visible only on this machine's Gitea) lets `verify.py phase5` read run results
     anonymously.
   - Private needs a token with `read:repository` scope (User settings → Applications) in
     `ci/.env` as `GITEA_READ_TOKEN`.
4. **Runner**: Site Administration → Actions → Runners → Create new runner, copy the
   registration token into `ci/.env` (copy `ci/.env.example`) as `RUNNER_TOKEN`. Then
   `uv run python scripts/ci.py up` again; the runner registers as `aperture-local` with labels
   `ci-python` and `ci-node`. The token can be removed from `ci/.env` afterwards.
5. **Remote and first push** (Git Credential Manager asks for your Gitea login once):

   ```
   git remote add gitea http://localhost:3000/<you>/aperture.git
   git push -u gitea main
   ```

After that, every push runs CI: egress-check, lint, typecheck, test, web, helm, then build
(all app images rebuilt with `--network=none` through the host Docker socket).

## Offline guarantees and where they are checked

| Guarantee | Enforced by | Checked by |
|---|---|---|
| No route out for Gitea, the runner or jobs | `aperture-ci` is `internal: true`; runner.yaml puts jobs on it | `egress-check` job on every run; `verify.py phase5` |
| Gitea never fetches or serves external content | `OFFLINE_MODE`, avatars/update checker/mirrors/migrations off, `DEFAULT_ACTIONS_URL=self` | `verify.py phase5` audits app.ini |
| Jobs download nothing | no `uses:`; job images are local (`bootstrap/build_deps.py ci-python ci-node`), never pulled | the jobs would fail on the internal network |
| Job images match the lockfiles | image label `aperture.lock-sha256` | `build` job: `build_deps.py --check` |
| Snapshot data (gitignored) is the pinned snapshot | volume `aperture-ci-data`, seeded by `ci.py` | `build` job: `data/snapshot/verify.py` |

Only the `build` job gets the Docker socket (it builds images); the runner itself needs it to
start job containers.
