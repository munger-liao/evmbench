#!/usr/bin/env bash
set -euo pipefail

# Runs the detect-only Pi agent and writes submission/audit.md.
#
# Expected environment:
# - AGENT_DIR: directory containing audit/, submission/
# - SUBMISSION_DIR: output dir (typically $AGENT_DIR/submission)
# - LOGS_DIR: log directory
# - PI_API_KEY: API key for LLM provider
# - PI_MODEL: model identifier
# - PI_BASE_URL: API base URL
# - EVM_BENCH_DETECT_MD: path to detect instructions markdown

: "${AGENT_DIR:?missing AGENT_DIR}"
: "${SUBMISSION_DIR:?missing SUBMISSION_DIR}"
: "${LOGS_DIR:?missing LOGS_DIR}"
: "${PI_API_KEY:?missing PI_API_KEY}"
: "${PI_MODEL:?missing PI_MODEL}"
: "${PI_BASE_URL:?missing PI_BASE_URL}"
: "${EVM_BENCH_DETECT_MD:?missing EVM_BENCH_DETECT_MD}"

mkdir -p "${SUBMISSION_DIR}" "${LOGS_DIR}"

# Keep runaway audits bounded by default.
TIMEOUT_SECONDS="${EVM_BENCH_PI_TIMEOUT_SECONDS:-10800}"
if ! [[ "${TIMEOUT_SECONDS}" =~ ^[0-9]+$ ]]; then
  echo "invalid EVM_BENCH_PI_TIMEOUT_SECONDS=${TIMEOUT_SECONDS}" >&2
  exit 2
fi

# Render instructions where Pi will read them.
cp "${EVM_BENCH_DETECT_MD}" "${AGENT_DIR}/AGENTS.md"

# Ensure a clean output.
rm -f "${SUBMISSION_DIR}/audit.json" "${SUBMISSION_DIR}/audit.md"

# Configure Pi to use our proxy
PI_CONFIG_DIR="${AGENT_DIR}/.pi/agent"
mkdir -p "${PI_CONFIG_DIR}"

cat > "${PI_CONFIG_DIR}/models.json" << EOF
{
  "providers": {
    "evmbench-proxy": {
      "baseUrl": "${PI_BASE_URL}",
      "api": "openai-completions",
      "apiKey": "${PI_API_KEY}",
      "models": [
        { "id": "${PI_MODEL}", "name": "${PI_MODEL}", "contextWindow": 128000 }
      ]
    }
  }
}
EOF

# Build the prompt
LAUNCHER_PROMPT='You are an expert smart contract auditor.
First read the AGENTS.md file for your detailed instructions.
Then proceed. Ensure to follow the submission instructions exactly.'

# Run Pi in non-interactive mode (print mode)
# -p processes the prompt and exits without interactive loop
timeout --signal=KILL "${TIMEOUT_SECONDS}s" pi \
  --no-session \
  --model "evmbench-proxy/${PI_MODEL}" \
  -p "${LAUNCHER_PROMPT}" \
  > "${LOGS_DIR}/agent.log" 2>&1 || true

# Check if audit output was created (prefer .json, fall back to .md)
if [[ ! -s "${SUBMISSION_DIR}/audit.json" ]] && [[ ! -s "${SUBMISSION_DIR}/audit.md" ]]; then
  echo "missing expected output: ${SUBMISSION_DIR}/audit.json or audit.md" >&2
  exit 2
fi
