#!/usr/bin/env bash
set -euo pipefail

# Runs the detect-only Pi agent and writes submission/audit.json.

: "${AGENT_DIR:?missing AGENT_DIR}"
: "${SUBMISSION_DIR:?missing SUBMISSION_DIR}"
: "${LOGS_DIR:?missing LOGS_DIR}"
: "${PI_MODEL:?missing PI_MODEL}"
: "${EVM_BENCH_DETECT_MD:?missing EVM_BENCH_DETECT_MD}"

PI_WIRE_API="${PI_WIRE_API:-openai-completions}"
PI_CONTEXT_WINDOW="${PI_CONTEXT_WINDOW:-128000}"
MAX_ATTEMPTS="${EVM_BENCH_PI_MAX_ATTEMPTS:-3}"

mkdir -p "${SUBMISSION_DIR}" "${LOGS_DIR}"

TIMEOUT_SECONDS="${EVM_BENCH_PI_TIMEOUT_SECONDS:-10800}"
if ! [[ "${TIMEOUT_SECONDS}" =~ ^[0-9]+$ ]]; then
  echo "invalid EVM_BENCH_PI_TIMEOUT_SECONDS=${TIMEOUT_SECONDS}" >&2
  exit 2
fi

# ── Build AGENTS.md with embedded source code ──
# Concatenate all in-scope .sol files directly into the instructions so the
# model sees the entire contract system in its first turn — no tool calls needed
# to read individual files, and cross-file analysis is possible from the start.

{
  cat "${EVM_BENCH_DETECT_MD}"
  echo ""
  echo "---"
  echo ""
  echo "# Source Code"
  echo ""
  echo "Below is the complete source code of all in-scope Solidity files."
  echo "Analyze them as a whole system — contracts interact with each other."
  echo ""

  AUDIT_DIR="${AGENT_DIR}/audit"
  FILE_COUNT=0
  TOTAL_CHARS=0
  MAX_CHARS=400000  # ~100k tokens, leave room for instructions + output

  # Collect .sol files, excluding common non-source dirs
  while IFS= read -r -d '' sol_file; do
    rel_path="${sol_file#${AUDIT_DIR}/}"

    # Skip test/lib/node_modules/macOS metadata directories and dotfiles
    case "${rel_path}" in
      test/*|tests/*|lib/*|node_modules/*|.git/*|__MACOSX/*|script/*|.*) continue ;;
    esac

    file_size=$(wc -c < "${sol_file}")
    NEW_TOTAL=$((TOTAL_CHARS + file_size))

    if [[ ${NEW_TOTAL} -gt ${MAX_CHARS} ]]; then
      echo ""
      echo "<!-- Truncated: remaining files omitted to fit context window. -->"
      echo "<!-- Use the read tool to examine any additional .sol files in audit/ -->"
      echo ""
      break
    fi

    echo "## File: ${rel_path}"
    echo ""
    echo '```solidity'
    cat "${sol_file}"
    echo '```'
    echo ""
    FILE_COUNT=$((FILE_COUNT + 1))
    TOTAL_CHARS=${NEW_TOTAL}
  done < <(find "${AUDIT_DIR}" -name '*.sol' -type f -print0 | sort -z)

  echo ""
  echo "<!-- Total: ${FILE_COUNT} files, ~${TOTAL_CHARS} chars -->"
} > "${AGENT_DIR}/AGENTS.md"

echo "Built AGENTS.md with ${FILE_COUNT:-0} embedded .sol files" >&2

# Prompt: model already has all code, just needs to analyze and write report
LAUNCHER_PROMPT='You are a smart contract security auditor. Read AGENTS.md — it contains your full instructions and all source code. Analyze the entire contract system for loss-of-funds vulnerabilities, considering cross-contract interactions. Write your JSON report to submission/audit.json following the format specified in AGENTS.md.'

# ── Configure Pi provider ──
PI_CONFIG_DIR="${AGENT_DIR}/.pi/agent"
mkdir -p "${PI_CONFIG_DIR}"

PROVIDER_NAME=""
RETRY_PROXY_PID=""

if [[ -n "${PI_PROVIDER:-}" ]]; then
  # Native provider (e.g. Google Gemini) — Pi handles API directly
  PROVIDER_NAME="${PI_PROVIDER}"
  echo "Using Pi native provider: ${PI_PROVIDER}, model: ${PI_MODEL}" >&2

elif [[ -n "${PI_AZURE_BASE_URL:-}" ]]; then
  # Direct Azure with 429-retry proxy
  RETRY_PROXY_PORT=19191

  cat > /tmp/_retry_proxy.py << 'PYEOF'
import sys, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError
UPSTREAM, API_KEY, PORT = sys.argv[1], sys.argv[2], int(sys.argv[3])
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        url = UPSTREAM.rstrip('/') + self.path
        for attempt in range(8):
            req = Request(url, data=body, method='POST')
            req.add_header('Content-Type', 'application/json')
            req.add_header('Authorization', f'Bearer {API_KEY}')
            try:
                r = urlopen(req, timeout=300)
                data = r.read()
                self.send_response(r.status)
                for k, v in r.getheaders():
                    if k.lower() not in ('transfer-encoding','connection'):
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)
                return
            except HTTPError as e:
                if e.code == 429 and attempt < 7:
                    ra = e.headers.get('Retry-After')
                    w = min(int(ra) if ra and ra.isdigit() else 15, 60)
                    print(f'[retry] 429 attempt {attempt+1}, wait {w}s', flush=True)
                    time.sleep(w)
                else:
                    data = e.read()
                    self.send_response(e.code)
                    for k, v in e.headers.items():
                        if k.lower() not in ('transfer-encoding','connection'):
                            self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(data)
                    return
    def log_message(self, *a): pass
HTTPServer(('127.0.0.1', PORT), H).serve_forever()
PYEOF

  # For openai-completions (e.g. Google Gemini), proxy path is different
  if [[ "${PI_WIRE_API}" == "openai-completions" ]]; then
    PROXY_UPSTREAM="${PI_AZURE_BASE_URL}"
  else
    PROXY_UPSTREAM="${PI_AZURE_BASE_URL}/v1"
  fi

  python3 /tmp/_retry_proxy.py "${PROXY_UPSTREAM}" "${PI_AZURE_API_KEY}" "${RETRY_PROXY_PORT}" \
    > "${LOGS_DIR}/retry_proxy.log" 2>&1 &
  RETRY_PROXY_PID=$!
  for _i in $(seq 1 20); do
    curl -s -o /dev/null "http://127.0.0.1:${RETRY_PROXY_PORT}/" 2>/dev/null && break
    sleep 0.2
  done

  PROVIDER_NAME="direct-route"
  cat > "${PI_CONFIG_DIR}/models.json" << EOF
{
  "providers": {
    "${PROVIDER_NAME}": {
      "baseUrl": "http://127.0.0.1:${RETRY_PROXY_PORT}",
      "api": "${PI_WIRE_API}",
      "apiKey": "local",
      "models": [
        { "id": "${PI_MODEL}", "name": "${PI_MODEL}", "contextWindow": ${PI_CONTEXT_WINDOW} }
      ]
    }
  }
}
EOF
  echo "Using Pi via retry proxy -> ${PI_AZURE_BASE_URL} (api=${PI_WIRE_API})" >&2

else
  : "${PI_API_KEY:?missing PI_API_KEY}"
  : "${PI_BASE_URL:?missing PI_BASE_URL}"
  PROVIDER_NAME="evmbench-proxy"
  cat > "${PI_CONFIG_DIR}/models.json" << EOF
{
  "providers": {
    "${PROVIDER_NAME}": {
      "baseUrl": "${PI_BASE_URL}",
      "api": "${PI_WIRE_API}",
      "apiKey": "${PI_API_KEY}",
      "models": [
        { "id": "${PI_MODEL}", "name": "${PI_MODEL}", "contextWindow": ${PI_CONTEXT_WINDOW} }
      ]
    }
  }
}
EOF
  echo "Using Pi via oai_proxy: api=${PI_WIRE_API} model=${PI_MODEL}" >&2
fi

# ── Run Pi with retry loop ──
for attempt in $(seq 1 "${MAX_ATTEMPTS}"); do
  rm -f "${SUBMISSION_DIR}/audit.json" "${SUBMISSION_DIR}/audit.md"
  echo "[attempt ${attempt}/${MAX_ATTEMPTS}] Running Pi agent..." >&2

  timeout --signal=KILL "${TIMEOUT_SECONDS}s" pi \
    --no-session \
    --model "${PROVIDER_NAME}/${PI_MODEL}" \
    -p "${LAUNCHER_PROMPT}" \
    > "${LOGS_DIR}/agent.log" 2>&1 || true

  if [[ -s "${SUBMISSION_DIR}/audit.json" ]]; then
    if python3 -c "import json; json.loads(open('${SUBMISSION_DIR}/audit.json').read(), strict=False)" 2>/dev/null; then
      echo "[attempt ${attempt}] audit.json created and valid" >&2
      break
    fi
  elif [[ -s "${SUBMISSION_DIR}/audit.md" ]]; then
    echo "[attempt ${attempt}] audit.md created" >&2
    break
  else
    echo "[attempt ${attempt}] No output, retrying..." >&2
  fi
done

# Clean up
[[ -n "${RETRY_PROXY_PID:-}" ]] && kill "${RETRY_PROXY_PID}" 2>/dev/null || true

if [[ ! -s "${SUBMISSION_DIR}/audit.json" ]] && [[ ! -s "${SUBMISSION_DIR}/audit.md" ]]; then
  echo "missing expected output after ${MAX_ATTEMPTS} attempts" >&2
  exit 2
fi
