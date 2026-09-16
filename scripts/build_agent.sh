#!/usr/bin/env bash
# Builds the AgentCore Runtime direct-code-deployment package for `src/agent`
# (task 8.1; design.md SS3, SS8 RQ-2 packaging flow).
#
# Produces build/agent/ (the `agent` dependency group's arm64 wheels installed at
# its root, plus src/agent's own modules copied on top so `main.py` sits at the
# root — never `agent/main.py`, matching the `EntryPoint: ["main.py"]` used by
# `infra/stacks/agent_stack.py`) and zips it deterministically into build/agent.zip,
# the file `infra/stacks/agent_stack.py`'s `aws_s3_assets.Asset` wraps.
#
# Verified against AWS documentation before writing this script: AgentCore Runtime
# direct code deployment supports **arm64 only** and requires Linux wheels
# (`aarch64-manylinux2014`), with dependencies and the entry file both at the zip
# root; the documented package size limit is 250 MB zipped / 750 MB unzipped —
# https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy-python.html
#
# Usage: scripts/build_agent.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/build/agent"
ZIP_PATH="${ROOT_DIR}/build/agent.zip"
REQUIREMENTS_PATH="${ROOT_DIR}/build/agent-requirements.txt"
# Documented AgentCore Runtime direct-code-deployment limit (zipped size).
MAX_ZIP_BYTES=$((250 * 1024 * 1024))

rm -rf "${BUILD_DIR}" "${ZIP_PATH}"
mkdir -p "${BUILD_DIR}"

echo "Exporting the 'agent' dependency group (strands-agents, bedrock-agentcore)..."
uv export --project "${ROOT_DIR}" --only-group agent --no-hashes -o "${REQUIREMENTS_PATH}"

echo "Installing arm64 (aarch64-manylinux2014) wheels into ${BUILD_DIR}..."
uv pip install \
  --target "${BUILD_DIR}" \
  --python-platform aarch64-manylinux2014 \
  --python-version 3.12 \
  --only-binary :all: \
  -r "${REQUIREMENTS_PATH}"

echo "Copying src/agent to the zip root..."
cp -R "${ROOT_DIR}/src/agent/." "${BUILD_DIR}/"

echo "Stripping __pycache__ (bytecode from this build machine is not portable)..."
find "${BUILD_DIR}" -type d -name "__pycache__" -prune -exec rm -rf {} +

echo "Zipping ${BUILD_DIR} -> ${ZIP_PATH}..."
(cd "${BUILD_DIR}" && zip -X -r "${ZIP_PATH}" . >/dev/null)

zip_size=$(stat -f%z "${ZIP_PATH}" 2>/dev/null || stat -c%s "${ZIP_PATH}")
echo "Built ${ZIP_PATH} (${zip_size} bytes)"

if [ "${zip_size}" -gt "${MAX_ZIP_BYTES}" ]; then
  echo "WARNING: ${ZIP_PATH} is ${zip_size} bytes, over the documented 250 MB" >&2
  echo "zipped limit for AgentCore Runtime direct code deployment." >&2
fi
