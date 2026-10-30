# Multi-agent system for failed GitLab pipelines

When a GitLab CI pipeline fails, this service finds the cause and proposes a fix.
Five agents work in a fixed order: triage, diagnosis (with web research when the
error is unknown), fixer and reviewer. A fix is only proposed once the reviewer
approves it, and it is published as a draft merge request, so a human always
decides before merging.

This is a proof of concept: one pipeline is analysed at a time.

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


## Iterative remediation (opt-in)

The default web flow is deliberately unchanged: `DRY_RUN=true` analyses a
failed pipeline and offers a patch for human approval. It does not create
branches, commits, or merge requests during analysis.

For a **test GitLab project** you can enable end-to-end iteration in the worker:

```dotenv
DRY_RUN=false
WAIT_FOR_PIPELINE=true
MAX_ROUNDS=3
PIPELINE_TIMEOUT_S=1800
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
