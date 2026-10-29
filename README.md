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

