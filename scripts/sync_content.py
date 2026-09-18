"""Manual content sync — upload local CV/portfolio content to S3 and start a
Knowledge Base ingestion job (`knowledge-base` spec, *Manual Sync Procedure*;
design.md SS11 Runbooks).

**Deviation from `tasks.md` 9.1/9.2's literal two-script split** (`scripts/
upload_content.sh` for a single-file upload, `scripts/sync_kb.py` for the
ingestion trigger). This session's Strict TDD constraint requires every
production module to be unit-testable with injected dependencies; a bash
upload script has no equivalent to `botocore.stub.Stubber` and would be
exercised only by `bash -n`/`shellcheck`, leaving the upload path's own logic
(extension/emptiness validation, S3 key derivation) untested. Folding upload
and ingestion into one Python script — both steps of the same operator
workflow — keeps 100 % of the operational logic under test, at no cost to the
runbook (still one command). `tasks.md` 9.1/9.2 and `design.md` SS11 are
updated inline to reflect this.

Local layout convention: `--content-dir` MUST mirror the S3 key layout below
`content/` — e.g. `<content-dir>/cv/resume.pdf`, `<content-dir>/portfolio/en/
about.md` — so the uploaded key is always `content/<relative path>`, matching
the `knowledge-base` spec's fixed `content/cv/*.pdf` / `content/portfolio/
{en,es}/*.md` layout exactly.

Bucket, Knowledge Base id, and data source id are resolved from the
`portfolio-agent-data` CloudFormation stack's outputs (`ContentBucketName`,
`KnowledgeBaseId`) and from `bedrock-agent`'s `ListDataSources`
(https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent_ListDataSources.html)
by name (`portfolio-agent-content`, `infra/stacks/data_stack.py`'s
`ContentDataSource`), unless overridden with `--bucket-name`/
`--knowledge-base-id`/`--data-source-id` — never hardcoded, per the
`infrastructure` spec's *No Secrets in Repository* / config-over-constant
convention already used by `src/api/config.py`.

Ingestion API surface verified against AWS documentation before writing this
script:
- `StartIngestionJob` (`knowledgeBaseId`, `dataSourceId` -> `ingestionJob`
  with `ingestionJobId`, `status`):
  https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent_StartIngestionJob.html
- `GetIngestionJob` (adds `dataSourceId`+`ingestionJobId` -> the same
  `ingestionJob` shape, `status` one of `STARTING | IN_PROGRESS | COMPLETE |
  FAILED | STOPPING | STOPPED`, plus `statistics`/`failureReasons`):
  https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent_GetIngestionJob.html
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

ALLOWED_EXTENSIONS = frozenset({".pdf", ".md"})
DEFAULT_STACK_NAME = "portfolio-agent-data"
DEFAULT_REGION = "us-east-1"
# Matches `infra/stacks/data_stack.py`'s `CfnDataSource(name=...)`.
DEFAULT_DATA_SOURCE_NAME = "portfolio-agent-content"
_TERMINAL_STATUSES = frozenset({"COMPLETE", "FAILED", "STOPPED"})
DEFAULT_POLL_INTERVAL_SECONDS = 10.0
DEFAULT_TIMEOUT_SECONDS = 600.0


class ContentValidationError(Exception):
    """Raised when `--content-dir` is missing, empty, or has a disallowed file."""


class IngestionTimeoutError(Exception):
    """Raised when an ingestion job does not reach a terminal state in time."""


@dataclass(frozen=True)
class IngestionResult:
    """A terminal (or last-observed) ingestion job outcome."""

    status: str
    ingestion_job_id: str
    statistics: Mapping[str, int]
    failure_reasons: Sequence[str]


def discover_content_files(content_dir: Path, allowed_extensions: frozenset[str] = ALLOWED_EXTENSIONS) -> list[Path]:
    """Return every file under `content_dir`, refusing to proceed if the
    directory is missing, empty, or contains a disallowed extension — a
    partial or bad upload must never reach S3.

    Hidden files/directories (any path component starting with `.`, e.g.
    `.git/`, `.secret.md`) are silently skipped. Symlinks are refused
    outright: a symlink can point anywhere on the operator's machine
    (including outside `content_dir`), so this never follows one — the error
    names only the offending path relative to `content_dir`, never the
    resolved target, which could itself be private.
    """
    if not content_dir.is_dir():
        raise ContentValidationError(f"content directory not found: {content_dir}")

    resolved_root = content_dir.resolve()
    files: list[Path] = []
    for path in content_dir.rglob("*"):
        relative = path.relative_to(content_dir)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if path.is_symlink():
            raise ContentValidationError(f"symlinks are not allowed in the content directory: {relative}")
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(resolved_root):
            raise ContentValidationError(f"file resolves outside the content directory: {relative}")
        files.append(path)

    files.sort()
    if not files:
        raise ContentValidationError(f"content directory is empty: {content_dir}")

    disallowed = [str(path) for path in files if path.suffix.lower() not in allowed_extensions]
    if disallowed:
        raise ContentValidationError(f"disallowed file extension(s) (only {sorted(allowed_extensions)}): {disallowed}")

    return files


def resolve_stack_outputs(cfn_client: Any, stack_name: str, output_keys: Iterable[str]) -> dict[str, str]:
    """Read the named CloudFormation stack's outputs, restricted to `output_keys`."""
    response = cfn_client.describe_stacks(StackName=stack_name)
    stacks = response.get("Stacks", [])
    if not stacks:
        raise LookupError(f"stack not found: {stack_name}")

    outputs = {item["OutputKey"]: item["OutputValue"] for item in stacks[0].get("Outputs", [])}
    missing = [key for key in output_keys if key not in outputs]
    if missing:
        raise LookupError(f"stack {stack_name} is missing output(s): {missing}")

    return {key: outputs[key] for key in output_keys}


def resolve_data_source_id(bedrock_agent_client: Any, knowledge_base_id: str, data_source_name: str) -> str:
    """Resolve the one data source named `data_source_name` in the Knowledge Base.

    There is no `DataSourceId` CloudFormation output (see `data_stack.py`), so
    this is looked up by name via `ListDataSources` instead of adding one.
    """
    response = bedrock_agent_client.list_data_sources(knowledgeBaseId=knowledge_base_id)
    matches = [
        summary for summary in response.get("dataSourceSummaries", []) if summary.get("name") == data_source_name
    ]
    if len(matches) != 1:
        raise LookupError(
            f"expected exactly one data source named {data_source_name!r} in "
            f"knowledge base {knowledge_base_id}, found {len(matches)}"
        )
    return str(matches[0]["dataSourceId"])


def upload_files(s3_client: Any, bucket_name: str, content_dir: Path, files: Sequence[Path]) -> list[str]:
    """Upload each file, preserving its path under `content_dir` as the S3 key
    under `content/` — this is what keeps the local layout and the
    `knowledge-base` spec's S3 layout identical."""
    keys: list[str] = []
    for path in files:
        key = f"content/{path.relative_to(content_dir).as_posix()}"
        s3_client.put_object(Bucket=bucket_name, Key=key, Body=path.read_bytes())
        keys.append(key)
    return keys


def start_and_wait_for_ingestion(
    bedrock_agent_client: Any,
    knowledge_base_id: str,
    data_source_id: str,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    now_fn: Callable[[], float] = time.monotonic,
) -> IngestionResult:
    """Start an ingestion job and poll `GetIngestionJob` until it reaches a
    terminal state (`COMPLETE`, `FAILED`, or `STOPPED`) or `timeout_seconds`
    elapses. `sleep_fn`/`now_fn` are injected so tests never sleep or depend
    on wall-clock time."""
    start_response = bedrock_agent_client.start_ingestion_job(
        knowledgeBaseId=knowledge_base_id, dataSourceId=data_source_id
    )
    job = start_response["ingestionJob"]
    ingestion_job_id = str(job["ingestionJobId"])
    status = str(job.get("status", "STARTING"))
    deadline = now_fn() + timeout_seconds

    while status not in _TERMINAL_STATUSES:
        if now_fn() >= deadline:
            raise IngestionTimeoutError(
                f"ingestion job {ingestion_job_id} did not reach a terminal state "
                f"within {timeout_seconds}s (last status: {status})"
            )
        sleep_fn(poll_interval_seconds)
        poll_response = bedrock_agent_client.get_ingestion_job(
            knowledgeBaseId=knowledge_base_id, dataSourceId=data_source_id, ingestionJobId=ingestion_job_id
        )
        job = poll_response["ingestionJob"]
        status = str(job["status"])

    return IngestionResult(
        status=status,
        ingestion_job_id=ingestion_job_id,
        statistics=job.get("statistics", {}),
        failure_reasons=job.get("failureReasons", []),
    )


def run_sync(
    *,
    content_dir: Path,
    s3_client: Any,
    bedrock_agent_client: Any,
    bucket_name: str,
    knowledge_base_id: str,
    data_source_id: str,
    sleep_fn: Callable[[float], None] = time.sleep,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    stdout: IO[str] = sys.stdout,
    stderr: IO[str] = sys.stderr,
) -> int:
    """Discover, upload, ingest, and report — the whole operator workflow.
    Returns a process exit code (0 success, 1 on any failure)."""
    try:
        files = discover_content_files(content_dir)
    except ContentValidationError as exc:
        print(str(exc), file=stderr)
        return 1

    uploaded_keys = upload_files(s3_client, bucket_name, content_dir, files)
    print(f"Uploaded {len(uploaded_keys)} file(s) to s3://{bucket_name}/content/", file=stdout)

    try:
        result = start_and_wait_for_ingestion(
            bedrock_agent_client,
            knowledge_base_id,
            data_source_id,
            sleep_fn=sleep_fn,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=timeout_seconds,
        )
    except IngestionTimeoutError as exc:
        print(str(exc), file=stderr)
        return 1

    print(f"Ingestion job {result.ingestion_job_id}: {result.status}", file=stdout)
    print(f"Statistics: {dict(result.statistics)}", file=stdout)

    if result.status != "COMPLETE":
        print(f"Failure reasons: {list(result.failure_reasons)}", file=stderr)
        return 1

    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content-dir", required=True, type=Path, help="local dir mirroring the content/ S3 layout")
    parser.add_argument("--stack-name", default=DEFAULT_STACK_NAME)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--data-source-name", default=DEFAULT_DATA_SOURCE_NAME)
    parser.add_argument("--bucket-name", default=None, help="override the ContentBucketName stack output")
    parser.add_argument("--knowledge-base-id", default=None, help="override the KnowledgeBaseId stack output")
    parser.add_argument("--data-source-id", default=None, help="override the ListDataSources lookup")
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL_SECONDS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    import boto3  # local import: keeps the pure functions above importable/testable with zero AWS SDK setup

    args = _parse_args(argv)

    need_bucket = args.bucket_name is None
    need_kb = args.knowledge_base_id is None
    if need_bucket or need_kb:
        cfn_client = boto3.client("cloudformation", region_name=args.region)
        keys = [k for k, needed in (("ContentBucketName", need_bucket), ("KnowledgeBaseId", need_kb)) if needed]
        outputs = resolve_stack_outputs(cfn_client, args.stack_name, keys)
    else:
        outputs = {}

    bucket_name = args.bucket_name or outputs["ContentBucketName"]
    knowledge_base_id = args.knowledge_base_id or outputs["KnowledgeBaseId"]

    bedrock_agent_client = boto3.client("bedrock-agent", region_name=args.region)
    data_source_id = args.data_source_id or resolve_data_source_id(
        bedrock_agent_client, knowledge_base_id, args.data_source_name
    )

    s3_client = boto3.client("s3", region_name=args.region)
    return run_sync(
        content_dir=args.content_dir,
        s3_client=s3_client,
        bedrock_agent_client=bedrock_agent_client,
        bucket_name=bucket_name,
        knowledge_base_id=knowledge_base_id,
        data_source_id=data_source_id,
        poll_interval_seconds=args.poll_interval,
        timeout_seconds=args.timeout,
    )


if __name__ == "__main__":
    sys.exit(main())
