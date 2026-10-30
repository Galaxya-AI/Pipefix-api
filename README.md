# Multi-agent system for failed GitLab pipelines

When a GitLab CI pipeline fails, this service finds the cause and proposes a fix.
Agents work in a fixed order: triage, diagnosis (with web research when the
error is unknown), fixer and reviewer. A fix is only proposed once the reviewer
approves it, and it is published as a draft merge request, so a human always
decides before merging.

This is a proof of concept: one pipeline is analysed at a time. A single
proposal is **not** proof that the pipeline has passed. Further failing pipelines
can be analysed independently. If a draft MR remains open for the *same*
pipeline, a new run still performs diagnosis; its fix uses an isolated branch
(`auto-fix/pipeline-<id>-retry-<run_id>`) so it does not overwrite the pending MR.
Both proposals require human review. Closed MRs are not treated as successful fixes.

**Important limitation:** no automatic merge or guarantee of a green pipeline.
When `WAIT_FOR_PIPELINE=false`, PipeFix proposes a draft MR but does not verify
that merging it will make the target pipeline pass. When enabled, the optional
pipeline wait/round loop applies only to the fix branch and respects `MAX_ROUNDS`.
If the failure comes from additional tests, run PipeFix on the newer failed
pipeline after updating the target branch; avoid merging incompatible fixes.


## Run

```bash
cp .env.example .env          # GitLab token, LLM key and the model of each agent
docker compose up --build -d
```

The service listens on http://localhost:8000.

## Web page (PipeFix)

Open http://localhost:8000 and enter your access code (given by the owner) and the
project address as shown in your browser, for example `https://gitlab.com/group/my-project`
(gitlab.com or a self-hosted GitLab; a pipeline page address also gives the pipeline number).
Optionally add a pipeline number (by default the latest failed pipeline) and a GitLab access token:

- no token is needed to analyse a public project whose pipelines are public;
- a private project needs a token with the `read_api` scope;
- creating the merge request needs a token with the `api` scope (asked for at that moment if it was left empty).

On a free GitLab.com account, project access tokens are not available: use a personal access token
(Preferences, Access tokens). The token is used for that run only and never stored.

The page shows the progress of each agent, then the cause, the fix, the review and the diff. Two actions
follow: download the patch, or create the draft merge request.

## API

| Method | Route | Purpose |
|---|---|---|
| GET  | `/health` | Service is up |
| GET  | `/api/v1/tools` | Tools available to the agents |
| POST | `/api/v1/runs` | Start an analysis |
| GET  | `/api/v1/runs/<run_id>` | Status, then report |
| GET  | `/api/v1/runs/<run_id>/events` | Steps of the agents |
| GET  | `/api/v1/runs/<run_id>/patch` | Proposed fix (diff) |
| POST | `/api/v1/webhooks/gitlab` | GitLab pipeline events: a failed pipeline starts an analysis |

The `/api/v1` routes need `Authorization: Bearer <API_TOKEN>`.

Example:

```bash
curl -X POST localhost:8000/api/v1/runs \
     -H "Authorization: Bearer $API_TOKEN" -H "Content-Type: application/json" \
     -d '{"project": "group/my-project"}'
# {"run_id": "20261026-175201-a3f2c1", "status_url": "/api/v1/runs/20261026-175201-a3f2c1"}

curl localhost:8000/api/v1/runs/20261026-175201-a3f2c1/patch -H "Authorization: Bearer $API_TOKEN"
```


<<<<<<< HEAD
## Diagnosing repeated CI failures

1. Check the failed pipeline ID and commit (`sha`) in GitLab. After an import fix,
   a **new** failed pipeline may expose previously hidden unit-test failures.
2. Analyse that new pipeline ID in PipeFix. A pending MR for an older pipeline
   is not evidence that the new failure was fixed.
3. If analysing the *same* ID again while an MR remains open, the report provides
   `previous_merge_request_url`, `fix_branch` and a warning. A new proposal is
   isolated from the previous one; compare both before merging either.
4. Download the proposed patch, inspect tests and CI logs, and create a draft MR
   only if you approve. Run GitLab CI again after merging; repeat on the next
   genuinely failed pipeline if necessary.

To prevent accidental recursive analysis, pipelines on the `auto-fix/` branch
prefix are excluded from automatic target selection. This is not a fully
self-healing loop and does not automatically resolve all errors in one pass.

## Changes in this update

- `src/workflow_remediation/remediation_workflow.py`: replaces the old
  `fix_already_proposed` early exit with independent reanalysis, pending-MR
  warnings, and tracking of the selected fix branch.
- `src/workflow_publication/publication_texts.py`: adds safe retry branch naming.
- `src/workflow_publication/publisher.py`: publishes a previously approved dry-run
  patch to the recorded independent branch rather than overwriting the old one.
- `tests/test_existing_merge_request.py`: adds regression coverage for pending
  MRs and standard first-time analysis.
- `templates/index.html`: shows the pending previous MR and a warning while allowing a new analysis.
- `README.md`: documents usage, limitations, repeat failures and the changed files.

## CI validation and iterative correction (safety contract)

This release adds CI verification metadata and repeated-failure detection:

- `validation.verified` is **true only** if GitLab reports `success` for the exact
  SHA written by PipeFix. A successful pipeline for another commit does not count.
- `validation.reason` reports `not_requested`, `dry_run`, `failed`,
  `pipeline_timeout`, `sha_mismatch`, or `success` where applicable.
- With `WAIT_FOR_PIPELINE=true`, the workflow can retry on a failed fix pipeline
  up to `MAX_ROUNDS`. If the same mandatory jobs fail again, it stops with
  `repeated_failure` and asks for human review instead of looping blindly.
- Published merge requests remain **drafts**. They are never automatically merged.
- Do **not** claim a fix is verified merely because a draft MR was opened.

Suggested configuration for controlled GitLab testing (use in a test project first):
=======
## Iterative remediation (opt-in)

The default web flow is deliberately unchanged: `DRY_RUN=true` analyses a
failed pipeline and offers a patch for human approval. It does not create
branches, commits, or merge requests during analysis.

For a **test GitLab project** you can enable end-to-end iteration in the worker:
>>>>>>> 9368335 (refactor: improve Flask modularity and iterative pipeline remediation)

```dotenv
DRY_RUN=false
WAIT_FOR_PIPELINE=true
MAX_ROUNDS=3
PIPELINE_TIMEOUT_S=1800
<<<<<<< HEAD
```

**Operational caveats:** `DRY_RUN=false` creates branches, commits and draft merge
requests. GitLab CI must be enabled for the fix branches, and the access token
must have the required permissions. GitLab pipeline jobs are the source of truth
for regression coverage: ensure `.gitlab-ci.yml` runs all existing tests rather
than only the test related to the proposed patch. MR pipelines and target branch
pipelines may differ. A green fix-branch pipeline does not prove the target
branch will remain green after merge. Review concurrent MRs and rebases manually.
An external GitLab/OpenShift end-to-end run is still required before production.

### Files changed in this release

- `src/workflow_remediation/remediation_workflow.py`: tracked CI outcomes, exact
  commit validation, repeated failed-job detection, and bounded retries.
- `src/workflow_publication/validation.py`: new pure validation and failure
  signature helpers.
- `tests/test_validation_policy.py`: tests for success, timeout, SHA mismatch,
  failed CI, and recurring failure signatures.
- `README.md`: documents configuration, behavior, and remaining limitations.
=======
MAX_CONTEXT_JOBS=5
CONTEXT_LOG_LINES=150
```

**This opt-in mode writes to GitLab.** It creates an isolated `auto-fix/`
branch (unique to each PipeFix run), commits a reviewer-approved proposal,
waits for GitLab CI to finish on that exact commit SHA, and if CI fails,
passes the new failing jobs and bounded log excerpts to the agents for the
next round. If CI succeeds, it opens **one draft merge request** for human
review. It never merges automatically. If CI times out, repeats the same
failure fingerprint or reaches the round limit, it stops **without opening
a draft MR** and records the attempted commits in the run report.

All rounds on the branch are cumulative. Each pipeline must trigger for the
`auto-fix/` branch and must run all required validation jobs; otherwise the
run may time out or provide insufficient evidence. `pipeline_fixed` means
that GitLab reported success for the latest proposed SHA, **not** a guarantee
that every possible bug is fixed or that production deployment is safe.

Each agent sees metadata for every failed job plus the bounded log excerpts
of the first `MAX_CONTEXT_JOBS` failing jobs. Longer logs are truncated by
`CONTEXT_LOG_LINES` per job, and only the jobs executed by GitLab are visible.
The next round can discover failures hidden by earlier failures or stage
conditions. The limit and reviewer are safety controls, not correctness
proofs. Before production, test GitLab permissions, CI rules, timeouts,
branch policies and OpenShift deployment separately.

For verification in a development environment with dependencies installed:

```bash
python -m compileall -q src app.py
ruff check .
pytest -q
```

**Known limits:** work can be blocked by CI rules that exclude auto-fix
branches; no automatic merge; only single-run iteration (not persistent
resumption after worker restart); a final global human review of the full
cumulative diff is still recommended. Web-form analysis uses dry-run by
default and manual publication of the saved patch does not perform the
iterative validation loop.

## Production readiness

See [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md) for the mandatory CI, security, OpenShift, and rollback checks. This archive is a release candidate until the GitLab and OpenShift integration gates have passed.


### Structure des routes Flask

`app.py` conserve la création de l'application, les hooks HTTP, les déclarations
de routes et leurs gestionnaires Flask. Les fonctions auxiliaires indépendantes
(access codes, parsing des URL GitLab, clés des runs, affichage des étapes et
lecture du lien de MR) ont été déplacées dans `src/web_support.py`.

Le lancement `gunicorn app:app` et les chemins d'API sont inchangés.
Les tests de formulaire ont été ajustés pour injecter le chemin du Secret
au niveau de son nouveau module. La réorganisation des gestionnaires de
routes en contrôleurs totalement minces reste une étape distincte, à vérifier
en intégration avec Flask avant déploiement.
>>>>>>> 9368335 (refactor: improve Flask modularity and iterative pipeline remediation)
