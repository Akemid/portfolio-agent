#!/usr/bin/env bash
# Builds the Lambda deployment package for `src/api` (task 8.3 prep; design.md
# SS4, SS9.2 packaging flow), mirroring `scripts/build_agent.sh`'s pattern.
#
# Produces build/lambda/ (this project's pinned boto3==1.43.90 vendored as
# arm64 wheels at its root — the pinned version, not whatever ships with the
# Lambda runtime, since the runtime-bundled boto3 may lack the
# bedrock-agentcore client per design.md SS8 RQ-5 — plus `src/api`'s own
# modules copied to `api/` underneath, preserving the package so its internal
# `from api.x import y` imports resolve) and zips it into build/lambda.zip
# with reproducible ordering (mtimes are not normalized), the file
# `infra/stacks/api_stack.py`'s `aws_lambda.Code.from_asset` wraps.
#
# Unlike `build_agent.sh`, no root shim is needed: the Lambda handler is
# configured as the dotted path `api.handler.lambda_handler`, which Lambda
# resolves through the `api` package directly.
#
# `uv export --no-dev --no-emit-project` (verified empirically: with no
# `--group`/`--only-group` flag, this pulls exactly the base
# `[project.dependencies]` — boto3 and its transitive deps — never the
# `agent`/`infra` groups) produces the Lambda's own dependency list, kept
# separate from `scripts/build_agent.sh`'s `--only-group agent` export.
#
# Usage: scripts/build_lambda.sh [build_dir]
#   build_dir defaults to build/lambda; override only for tests (the zip and
#   requirements files are always written next to it).
# Env: BUILD_SKIP_DEPS=1 skips the export/install steps (test-only fast path).
#   BUILD_MAX_ZIP_BYTES overrides the 250 MB size-limit check (test-only).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Optional first argument overrides the build directory (test-only; production
# usage takes no arguments). The zip/requirements outputs always live next to
# whichever build directory is in effect.
BUILD_DIR="${1:-${ROOT_DIR}/build/lambda}"
OUT_DIR="$(dirname "${BUILD_DIR}")"
ZIP_PATH="${OUT_DIR}/lambda.zip"
REQUIREMENTS_PATH="${OUT_DIR}/lambda-requirements.txt"
# Lambda deployment package limit (unzipped): 250 MB — same documented ceiling
# this repo already applies to the AgentCore direct-deploy package.
MAX_ZIP_BYTES="${BUILD_MAX_ZIP_BYTES:-$((250 * 1024 * 1024))}"

rm -rf "${BUILD_DIR}" "${ZIP_PATH}"
mkdir -p "${BUILD_DIR}"

if [ -z "${BUILD_SKIP_DEPS:-}" ]; then
  echo "Exporting the Lambda's runtime dependencies (boto3; no dev/agent/infra groups)..."
  uv export --project "${ROOT_DIR}" --no-dev --no-emit-project --no-hashes -o "${REQUIREMENTS_PATH}"

  echo "Installing arm64 (aarch64-manylinux2014) wheels into ${BUILD_DIR}..."
  uv pip install \
    --target "${BUILD_DIR}" \
    --python-platform aarch64-manylinux2014 \
    --python-version 3.12 \
    --only-binary :all: \
    -r "${REQUIREMENTS_PATH}"
else
  # Test-only fast path (BUILD_SKIP_DEPS=1): the caller's environment already
  # has boto3 importable (e.g. via `uv run`), so skip the network-dependent
  # export/install steps entirely.
  echo "BUILD_SKIP_DEPS=1: skipping dependency export/install."
fi

echo "Copying src/api to ${BUILD_DIR}/api (preserving the package so its"
echo "internal 'from api.x import y' imports resolve)..."
mkdir -p "${BUILD_DIR}/api"
cp -R "${ROOT_DIR}/src/api/." "${BUILD_DIR}/api/"

echo "Stripping __pycache__ (bytecode from this build machine is not portable)..."
find "${BUILD_DIR}" -type d -name "__pycache__" -prune -exec rm -rf {} +

echo "Zipping ${BUILD_DIR} -> ${ZIP_PATH}..."
(cd "${BUILD_DIR}" && zip -X -r "${ZIP_PATH}" . >/dev/null)

zip_size=$(stat -f%z "${ZIP_PATH}" 2>/dev/null || stat -c%s "${ZIP_PATH}")
echo "Built ${ZIP_PATH} (${zip_size} bytes)"

if [ "${zip_size}" -gt "${MAX_ZIP_BYTES}" ]; then
  echo "ERROR: ${ZIP_PATH} is ${zip_size} bytes, over the documented 250 MB" >&2
  echo "zipped limit for a Lambda deployment package." >&2
  exit 1
fi
