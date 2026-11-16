# PipeFix API

When a GitLab CI pipeline fails, PipeFix finds the cause and proposes a fix. Four agents work
in a fixed order: **triage**, **diagnosis** (with web research when the error is unknown),
**fixer** and **reviewer**. A fix is only proposed once the reviewer approves it, and it is
published as a **draft** merge request: a human always decides before merging.

This repository is the backend: agents, GitLab access, runs and a JSON API. The web interface
is a separate application, pipefix-web, in its own repository. The contract
between them is [`openapi.yaml`](openapi.yaml), also served at `GET /api/openapi.yaml`. The API
works without the interface (curl, scripts, GitLab webhooks).

## Quick start

**Checks, without any key, GitLab access or LLM** (the agents' models and GitLab are simulated in
the tests):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q          # unit, workflow and API contract tests
ruff check .
mypy src app.py
```

**Run the API locally** (it needs a GitLab token and an LLM provider, see `.env.example`):

```bash
cp .env.example .env                      # fill in GITLAB_TOKEN, OPENAI_API_KEY or OLLAMA_BASE_URL, models
echo "me:my-code" > ~/pipefix-access-codes # access codes of the visitor API, one "name:code" per line
set -a; source .env; set +a
export ACCESS_CODES_FILE=~/pipefix-access-codes
PYTHONPATH=src python app.py              # http://localhost:8000
```

Or with Docker (API, Redis queue and worker): `docker compose up --build -d`.

A local model can replace OpenAI: set `OLLAMA_BASE_URL=http://localhost:11434/v1` and
`<AGENT>_LLM_PROVIDER=ollama` with a model that supports tool calls.

## Project structure

```
app.py                  Flask application: every route of the API
openapi.yaml            API contract (OpenAPI 3.1), checked by tests/test_api_contract.py
src/
  agents/               the agents: triage, diagnosis, research, fixer, reviewer, and their outputs
  agent_base/           settings of an agent; agent_runner/: runs an agent until it gives a valid output
  agent_errors/         errors raised while running agents
  llm/                  LLM layer: providers (OpenAI, Ollama), retries, call spacing, token usage
  tools/                tool registry and the tools of the agents; mcp_runtime/: tools from MCP servers
  connectors/           GitLab and web search connectors, and a template for a new one
  changeset/            proposed edits (all or nothing), static check (pyflakes), ruff autofix
  text_tools/           log cleaning, error lines and code of the failed tests, without an LLM call
  workflow_analysis/    one round: triage to review, reuse of a known fix, earlier attempts
  workflow_remediation/ the rounds until the pipeline passes, and their results
  workflow_publication/ commit, draft merge request, pipeline wait, failure notification
  app_runs/             run store, launchers (threads, at most MAX_PARALLEL_RUNS), daily limit
  redis_jobs/           Redis queue and branch lock, for several pods or workers
  visitor_api/          access codes, inputs, views and CORS of the visitor API
  journal/              journal of everything the agents do during a run
  policy/               data policy: confidential projects never go to external LLMs
  logging_module/       logs on the standard output, errors to Sentry (SENTRY_DSN)
  startup/              settings read from the environment, services built at start-up
tests/                  offline tests (no network, no key)
openshift/              Deployment, Service, Route, volume and setup.sh for a new namespace
```

## Visitor API (used by the web interface)

A visitor gives an access code (given by the owner; `ACCESS_REQUEST_EMAIL` adds an "Ask for one by email"
link in the interface), the address of a GitLab project as shown in the browser, for example
`https://gitlab.com/group/my-project` (gitlab.com or a self-hosted GitLab; a pipeline page address also gives
the pipeline number), an optional pipeline number (by default the latest failed pipeline) and a GitLab
access token, which is required:

- the `read_api` scope is enough to analyse;
- creating the merge request or fixing until the pipeline passes needs the `api` scope.

On a free GitLab.com account, project access tokens are not available: use a personal access token
(Preferences, Access tokens). The token is used for that run only and never stored.

| Method | Route | Purpose |
|---|---|---|
| GET  | `/web/config` | Public settings of the interface (access code request address, daily limit) |
| POST | `/web/runs` | Analyse a project; answers a run id and a run key |
| GET  | `/web/runs/<run_id>` | Progress of each agent, result, rounds and patch (`X-Run-Key`) |
| POST | `/web/runs/<run_id>/publish` | Publish the fix as a draft merge request (`X-Run-Key`, `X-Gitlab-Token`) |
| POST | `/web/runs/<run_id>/fix-until-green` | Commit the fix and fix round after round until the pipeline passes |

**Interface on another domain.** List its address in `CORS_ORIGINS`, for example
`CORS_ORIGINS=https://pipefix.example.com,http://localhost:5173`. Only the visitor API, `/health` and
`/api/openapi.yaml` are open to those origins; the server API below never is. Empty (default): same
domain only, for example when a route sends `/web` and `/api` to the API and `/` to the interface.

**On OpenShift**, `openshift/setup.sh` gives the API the paths `/web` and `/api` of `PIPEFIX_HOST`; the setup
of pipefix-web gives it `/` of the same host. The interface then calls the API on its own domain, without
CORS. Deploy the interface first: until its route exists, `/` of the host has no page.

Several visitors can use PipeFix at the same time. At most `MAX_PARALLEL_RUNS` runs (default 2) work at
once in the pod; the others wait as "queued" and start by themselves. Two runs on the same project never
work at the same time. Each access code may start `DAILY_RUNS_PER_VISITOR` analyses a day (default 10);
after that the API answers 429 with a clear message. The LLM calls of the whole pod are spaced by
`LLM_MIN_INTERVAL_S` (0.5 s), so several runs do not hit the provider's rate limit together. No Redis or
database is needed for one pod.

## Server API

| Method | Route | Purpose |
|---|---|---|
| GET  | `/` | Name and version of the API |
| GET  | `/health` | Service is up |
| GET  | `/api/openapi.yaml` | Description of the whole API (OpenAPI 3.1) |
| GET  | `/api/v1/tools` | Tools available to the agents |
| POST | `/api/v1/runs` | Start an analysis |
| GET  | `/api/v1/runs/<run_id>` | Status, then report |
| GET  | `/api/v1/runs/<run_id>/events` | Steps of the agents |
| GET  | `/api/v1/runs/<run_id>/patch` | Proposed fix (diff) |
| POST | `/api/v1/webhooks/gitlab` | GitLab pipeline events: a failed pipeline starts an analysis |

The `/api/v1` routes need `Authorization: Bearer <API_TOKEN>`; they answer 503 while `API_TOKEN`
is empty (the visitor API uses access codes and keeps working). Generate one with `openssl rand -hex 32`.

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

**Visitor API.** `POST /web/runs/<run_id>/fix-until-green` commits the fix the visitor saw,
then runs the rounds; `GET /web/runs/<run_id>` gives each round: the error lines of the
failed jobs, the cause, the fix, the commit and the next pipeline. `POST .../publish` still
publishes the fix alone, without waiting for CI. Both need a token with the `api` scope,
kept only while the run lasts.

**API.** `POST /api/v1/runs` with `"apply": true` runs the same rounds when
`WAIT_FOR_PIPELINE=true`.

Settings (OpenShift: the `pipefix-env` secret, see `openshift/setup.env.example`):

```dotenv
MAX_ROUNDS=5
WAIT_FOR_PIPELINE=true
PIPELINE_TIMEOUT_S=900
AGENT_MAX_TURNS=10
MAX_LLM_CALLS_PER_RUN=50
MAX_CONTEXT_JOBS=5
CONTEXT_LOG_LINES=150
```

An empty value uses the default.

**LLM calls.** Each answer of an agent is one LLM call, so the agents are built to answer
in as few calls as possible: the fixer and the reviewer receive the content of the files
(`TASK_FILE_MAX_CHARS`, `TASK_MAX_FILES`) instead of reading them; the fixer sends every
edit in one `propose_edits` call (all applied or none) and finishes in the same answer;
the error list puts the `FAILED tests/...` lines first, so the diagnosis sees every failed
test and fewer rounds are needed. The journal records the calls of each round per agent
(event `llm_calls`), with the tokens of the run and the part the provider read from its
prompt cache, billed much cheaper (`run_tokens.cached_input_tokens`; also in the run report). When GitLab reports an infrastructure `failure_reason` for every
failed job (`runner_system_failure`, `stuck_or_timeout_failure`...), the triage result
`infra_flaky` is known without an LLM call; every other failure goes to the triage agent.
Lint and format errors that ruff fixes on its own (the rules the log reports, safe fixes only, with the project's ruff configuration) are fixed before any agent runs: when that fixes every error, the round needs no LLM call (`AUTOFIX_RUFF`). When the same failure comes back on the same commit (a retried pipeline, a webhook sent twice), the fix already approved is reused without any LLM call, unless the pipeline after it failed (`REUSE_FIXES`, `REUSE_TTL_S`). `AGENT_MAX_TURNS` and `MAX_LLM_CALLS_PER_RUN` are safety limits.

To see every error in one pipeline, the CI of the
analysed project should not stop at the first one, for example:
`python -m pytest -rfE --continue-on-collection-errors` and a separate `ruff check` job
in the same stage.

**Known limits:** CI rules must run the required jobs on `auto-fix/` branches; a pod
restart stops a running loop (the commits already made stay on the branch; the runs
themselves are kept on the `pipefix-runs` persistent volume);
`pipeline_fixed` means GitLab reported success for the last commit, not that every bug
is fixed. Review the full cumulative diff before merging.

## Deployment

The GitLab pipeline (`.gitlab-ci.yml`) uses the group template: security scans, tests, Docker
image, then deployment to OpenShift (DEV on every merge into `develop`, REC on a `REC_NNN` tag).
First time in a namespace: copy `openshift/setup.env.example` to `openshift/setup.env`, fill it
in, then run `./openshift/setup.sh` after `oc login`. It creates the secrets (`pipefix-env`,
registry, access codes) and the routes `/web` and `/api` of `PIPEFIX_HOST`.

