#!/usr/bin/env bash
# Builds the AgentCore Runtime direct-code-deployment package for `src/agent`
# (task 8.1; design.md SS3, SS8 RQ-2 packaging flow).
#
# Produces build/agent/ (the `agent` dependency group's arm64 wheels installed at
# its root, `src/agent`'s own modules copied to `agent/` underneath — preserving
# the package so its internal `from agent.x import y` imports resolve — plus a
# root `main.py` shim (`scripts/agent_entrypoint.py`, `from agent.main import
# app`) so the zip root itself matches the `EntryPoint: ["main.py"]` used by
# `infra/stacks/agent_stack.py`) and zips it into build/agent.zip with
# reproducible ordering (mtimes are not normalized), the file
# `infra/stacks/agent_stack.py`'s `aws_s3_assets.Asset` wraps.
#
# Verified against AWS documentation before writing this script: AgentCore Runtime
# direct code deployment supports **arm64 only** and requires Linux wheels
# (`aarch64-manylinux2014`), with dependencies and the entry file both at the zip
# root; the documented package size limit is 250 MB zipped / 750 MB unzipped —
# https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy-python.html
#
# Usage: scripts/build_agent.sh [build_dir]
#   build_dir defaults to build/agent; override only for tests (the zip and
#   requirements files are always written next to it).
# Env: BUILD_SKIP_DEPS=1 skips the export/install steps (test-only fast path).
#   BUILD_MAX_ZIP_BYTES overrides the 250 MB size-limit check (test-only).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Optional first argument overrides the build directory (test-only; production
# usage takes no arguments). The zip/requirements outputs always live next to
# whichever build directory is in effect.
BUILD_DIR="${1:-${ROOT_DIR}/build/agent}"
OUT_DIR="$(dirname "${BUILD_DIR}")"
ZIP_PATH="${OUT_DIR}/agent.zip"
REQUIREMENTS_PATH="${OUT_DIR}/agent-requirements.txt"
# Documented AgentCore Runtime direct-code-deployment limit (zipped size).
MAX_ZIP_BYTES="${BUILD_MAX_ZIP_BYTES:-$((250 * 1024 * 1024))}"

rm -rf "${BUILD_DIR}" "${ZIP_PATH}"
mkdir -p "${BUILD_DIR}"

if [ -z "${BUILD_SKIP_DEPS:-}" ]; then
  echo "Exporting the 'agent' dependency group (strands-agents, bedrock-agentcore)..."
  uv export --project "${ROOT_DIR}" --only-group agent --no-hashes -o "${REQUIREMENTS_PATH}"

  echo "Installing arm64 (aarch64-manylinux2014) wheels into ${BUILD_DIR}..."
  uv pip install \
    --target "${BUILD_DIR}" \
    --python-platform aarch64-manylinux2014 \
    --python-version 3.12 \
    --only-binary :all: \
    -r "${REQUIREMENTS_PATH}"
else
  # Test-only fast path (BUILD_SKIP_DEPS=1): the caller's environment already
  # has the `agent` dependency group importable (e.g. via `uv run`), so
  # skip the network-dependent export/install steps entirely.
  echo "BUILD_SKIP_DEPS=1: skipping dependency export/install."
fi

echo "Copying src/agent to ${BUILD_DIR}/agent (preserving the package so its"
echo "internal 'from agent.x import y' imports resolve)..."
mkdir -p "${BUILD_DIR}/agent"
cp -R "${ROOT_DIR}/src/agent/." "${BUILD_DIR}/agent/"

echo "Writing the zip-root main.py entrypoint shim (scripts/agent_entrypoint.py)..."
cp "${ROOT_DIR}/scripts/agent_entrypoint.py" "${BUILD_DIR}/main.py"

echo "Stripping __pycache__ (bytecode from this build machine is not portable)..."
find "${BUILD_DIR}" -type d -name "__pycache__" -prune -exec rm -rf {} +

echo "Zipping ${BUILD_DIR} -> ${ZIP_PATH}..."
(cd "${BUILD_DIR}" && zip -X -r "${ZIP_PATH}" . >/dev/null)

zip_size=$(stat -f%z "${ZIP_PATH}" 2>/dev/null || stat -c%s "${ZIP_PATH}")
echo "Built ${ZIP_PATH} (${zip_size} bytes)"

if [ "${zip_size}" -gt "${MAX_ZIP_BYTES}" ]; then
  echo "ERROR: ${ZIP_PATH} is ${zip_size} bytes, over the documented 250 MB" >&2
  echo "zipped limit for AgentCore Runtime direct code deployment." >&2
  exit 1
fi
