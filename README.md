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


## Fix until the pipeline passes

A failed pipeline often hides errors: an import error stops pytest before any test
runs, a failed job skips the next stages. PipeFix handles this in rounds on one
`auto-fix/` branch:

1. the agents fix every error visible in the logs of the failed jobs;
2. the fix is committed (the first commit also creates the branch, so GitLab does
   not run a useless pipeline on the unchanged code);
3. PipeFix waits for GitLab CI on that exact commit;
4. red pipeline: the new errors start the next round; green pipeline: one **draft**
   merge request opens. It is never merged automatically.

The run stops without a merge request on `max_rounds_reached`, `repeated_failure`
(the same error lines came back), `pipeline_timeout` or a SHA mismatch; the fixes
already made stay on the branch.

**Web page.** After an analysis, *Fix until the pipeline passes* commits the fix the
visitor saw, then runs the rounds; the page shows each round: the error lines of the
failed jobs, the cause, the fix, the commit and the next pipeline. *Create merge request*
still publishes the shown fix alone, without waiting for CI. Both need a token with
the `api` scope, kept only while the run lasts.

**API.** `POST /api/v1/runs` with `"apply": true` runs the same rounds when
`WAIT_FOR_PIPELINE=true`.

Settings (OpenShift: the `pipefix-env` secret, see `openshift/setup.env.example`):

```dotenv
MAX_ROUNDS=5
WAIT_FOR_PIPELINE=true
PIPELINE_TIMEOUT_S=900
AGENT_MAX_TURNS=30
MAX_LLM_CALLS_PER_RUN=200
MAX_CONTEXT_JOBS=5
CONTEXT_LOG_LINES=150
```

An empty value uses the default. To see every error in one pipeline, the CI of the
analysed project should not stop at the first one, for example:
`python -m pytest -rfE --continue-on-collection-errors` and a separate `ruff check` job
in the same stage.

**Known limits:** CI rules must run the required jobs on `auto-fix/` branches; a pod
restart stops a running loop (the commits already made stay on the branch);
`pipeline_fixed` means GitLab reported success for the last commit, not that every bug
is fixed. Review the full cumulative diff before merging.

For verification in a development environment with dependencies installed:

```bash
python -m compileall -q src app.py
ruff check .
mypy src app.py
pytest -q
```

### Structure des routes Flask

`app.py` conserve la création de l'application, les hooks HTTP, les déclarations
de routes et leurs gestionnaires Flask. Les fonctions auxiliaires indépendantes
(access codes, parsing des URL GitLab, clés des runs, affichage des étapes et des
tours, lecture du lien de MR) sont dans `src/web_support.py`.
