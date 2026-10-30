#!/usr/bin/env bash
# Recreates the PipeFix setup in an empty OpenShift namespace. Run after "oc login": ./openshift/setup.sh
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
settings="$here/setup.env"
codes="$here/access-codes"

if [ ! -f "$settings" ]; then
  echo "Missing $settings: copy openshift/setup.env.example and fill it in." >&2
  exit 1
fi
set -a
# shellcheck source=/dev/null
. "$settings"
set +a

namespace="$(oc project -q)"
echo "Namespace: $namespace"

oc create serviceaccount gitlab-deployer --dry-run=client -o yaml | oc apply -f -
oc create rolebinding gitlab-deployer-edit --clusterrole=edit --serviceaccount="$namespace:gitlab-deployer" \
  --dry-run=client -o yaml | oc apply -f -
oc apply -f - <<YAML
apiVersion: v1
kind: Secret
metadata:
  name: gitlab-deployer-token
  annotations:
    kubernetes.io/service-account.name: gitlab-deployer
type: kubernetes.io/service-account-token
YAML

oc create secret docker-registry gitlab-registry-pipefix --docker-server=registry.gitlab.com \
  --docker-username="$REGISTRY_USERNAME" --docker-password="$REGISTRY_TOKEN" \
  --dry-run=client -o yaml | oc apply -f -

# Empty values are skipped: an empty number would stop the application at start-up.
app_keys=(
  GITLAB_URL GITLAB_TOKEN OPENAI_API_KEY API_TOKEN DRY_RUN CONNECTORS SERPER_API_KEY
  TRIAGE_LLM_PROVIDER TRIAGE_LLM_MODEL DIAGNOSIS_LLM_PROVIDER DIAGNOSIS_LLM_MODEL
  RESEARCH_LLM_PROVIDER RESEARCH_LLM_MODEL FIXER_LLM_PROVIDER FIXER_LLM_MODEL
  REVIEWER_LLM_PROVIDER REVIEWER_LLM_MODEL
  MAX_ROUNDS WAIT_FOR_PIPELINE PIPELINE_TIMEOUT_S MAX_FIX_ATTEMPTS MAX_CONTEXT_JOBS CONTEXT_LOG_LINES
  AGENT_MAX_TURNS MAX_LLM_CALLS_PER_RUN
)
env_file="$(mktemp)"
trap 'rm -f "$env_file"' EXIT
for key in "${app_keys[@]}"; do
  if [ -n "${!key:-}" ]; then
    printf '%s=%s\n' "$key" "${!key}" >> "$env_file"
  fi
done
oc create secret generic pipefix-env --from-env-file="$env_file" --dry-run=client -o yaml | oc apply -f -

if [ -f "$codes" ]; then
  oc create secret generic pipefix-access --from-file=access-codes="$codes" --dry-run=client -o yaml | oc apply -f -
else
  echo "No $codes: every visitor will be refused until pipefix-access exists." >&2
fi

if [ -n "${PIPEFIX_HOST:-}" ]; then
  oc apply -f - <<YAML
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: pipefix-xulys
  labels:
    app: pipefix
spec:
  host: $PIPEFIX_HOST
  to:
    kind: Service
    name: pipefix
  port:
    targetPort: http
YAML
fi

echo
echo "Done. In GitLab, Settings > CI/CD > Variables, update:"
echo "  OPENSHIFT_PROJECT = $namespace"
echo "  OPENSHIFT_SERVER  = $(oc whoami --show-server)"
echo "  OPENSHIFT_TOKEN   = output of: oc get secret gitlab-deployer-token -o jsonpath='{.data.token}' | base64 -d"
echo "Then run a new pipeline on develop."
