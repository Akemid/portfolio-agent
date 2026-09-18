"""Unit tests for `scripts/sync_content.py` (tasks.md 9.1/9.2 merged; see the
module docstring's deviation note for why upload+ingestion is one script).

Covers `knowledge-base` spec, *Manual Sync Procedure*. Pure-logic functions
(`discover_content_files`, `resolve_stack_outputs`, `resolve_data_source_id`,
`upload_files`, `start_and_wait_for_ingestion`, `run_sync`) are exercised with
hand-written fakes for fast, fine-grained control; the exact wire contract for
each boto3 call is additionally verified with `botocore.stub.Stubber` against
the real (unconnected) `cloudformation`/`bedrock-agent` service models — no
network access.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber
from sync_content import (
    ContentValidationError,
    IngestionResult,
    IngestionTimeoutError,
    discover_content_files,
    resolve_data_source_id,
    resolve_stack_outputs,
    run_sync,
    start_and_wait_for_ingestion,
    upload_files,
)


class _FakeS3Client:
    """Records every `put_object` call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {}


class _FakeBedrockAgentClient:
    """Scripts a `start_ingestion_job` call followed by N `get_ingestion_job`
    polls, returning one canned job dict per call in order."""

    def __init__(self, job_sequence: list[dict[str, Any]]) -> None:
        self._job_sequence = list(job_sequence)
        self.start_calls: list[dict[str, Any]] = []
        self.poll_calls: list[dict[str, Any]] = []

    def start_ingestion_job(self, **kwargs: Any) -> dict[str, Any]:
        self.start_calls.append(kwargs)
        return {"ingestionJob": self._job_sequence[0]}

    def get_ingestion_job(self, **kwargs: Any) -> dict[str, Any]:
        self.poll_calls.append(kwargs)
        index = len(self.poll_calls)
        return {"ingestionJob": self._job_sequence[index]}


def _job(status: str, **extra: Any) -> dict[str, Any]:
    return {
        "ingestionJobId": "job-1",
        "knowledgeBaseId": "kb-1",
        "dataSourceId": "ds-1",
        "status": status,
        "statistics": extra.get("statistics", {}),
        "failureReasons": extra.get("failureReasons", []),
    }


# --- discover_content_files -------------------------------------------------


def test_discover_content_files_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(ContentValidationError, match="not found"):
        discover_content_files(tmp_path / "nope")


def test_discover_content_files_rejects_empty_directory(tmp_path: Path) -> None:
    with pytest.raises(ContentValidationError, match="empty"):
        discover_content_files(tmp_path)


def test_discover_content_files_rejects_disallowed_extension(tmp_path: Path) -> None:
    (tmp_path / "cv").mkdir()
    (tmp_path / "cv" / "resume.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "cv" / "notes.txt").write_text("nope")

    with pytest.raises(ContentValidationError, match="disallowed"):
        discover_content_files(tmp_path)


def test_discover_content_files_returns_sorted_pdf_and_md_files(tmp_path: Path) -> None:
    (tmp_path / "cv").mkdir()
    (tmp_path / "portfolio" / "en").mkdir(parents=True)
    pdf = tmp_path / "cv" / "resume.pdf"
    md = tmp_path / "portfolio" / "en" / "about.md"
    pdf.write_bytes(b"%PDF-1.4")
    md.write_text("# About")

    files = discover_content_files(tmp_path)

    assert files == sorted([pdf, md])


def test_discover_content_files_rejects_symlink_without_leaking_target_path(tmp_path: Path) -> None:
    """A symlink could point anywhere on the operator's machine — refuse it
    outright rather than silently uploading whatever it resolves to, and
    never print the resolved (possibly private) target in the error."""
    outside_secret = tmp_path / "outside_secret.pdf"
    outside_secret.write_bytes(b"%PDF-1.4")
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    (content_dir / "resume.pdf").symlink_to(outside_secret)

    with pytest.raises(ContentValidationError) as exc_info:
        discover_content_files(content_dir)

    message = str(exc_info.value)
    assert "resume.pdf" in message
    assert str(outside_secret) not in message


def test_discover_content_files_skips_hidden_files(tmp_path: Path) -> None:
    (tmp_path / ".secret.md").write_text("hidden")
    (tmp_path / "cv").mkdir()
    valid = tmp_path / "cv" / "cv.pdf"
    valid.write_bytes(b"%PDF-1.4")

    files = discover_content_files(tmp_path)

    assert files == [valid]


def test_discover_content_files_skips_hidden_directories(tmp_path: Path) -> None:
    hidden_dir = tmp_path / ".git"
    hidden_dir.mkdir()
    (hidden_dir / "config.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "cv").mkdir()
    valid = tmp_path / "cv" / "cv.pdf"
    valid.write_bytes(b"%PDF-1.4")

    files = discover_content_files(tmp_path)

    assert files == [valid]


# --- resolve_stack_outputs ---------------------------------------------------


def test_resolve_stack_outputs_returns_requested_keys() -> None:
    client = boto3.client("cloudformation", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x")
    stubber = Stubber(client)
    stubber.add_response(
        "describe_stacks",
        {
            "Stacks": [
                {
                    "StackName": "portfolio-agent-data",
                    "StackStatus": "CREATE_COMPLETE",
                    "CreationTime": "2026-01-01T00:00:00Z",
                    "Outputs": [
                        {"OutputKey": "ContentBucketName", "OutputValue": "bucket-1"},
                        {"OutputKey": "KnowledgeBaseId", "OutputValue": "kb-1"},
                    ],
                }
            ]
        },
        {"StackName": "portfolio-agent-data"},
    )
    stubber.activate()

    outputs = resolve_stack_outputs(client, "portfolio-agent-data", ["ContentBucketName", "KnowledgeBaseId"])

    assert outputs == {"ContentBucketName": "bucket-1", "KnowledgeBaseId": "kb-1"}
    stubber.deactivate()


def test_resolve_stack_outputs_raises_on_missing_output() -> None:
    client = boto3.client("cloudformation", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x")
    stubber = Stubber(client)
    stubber.add_response(
        "describe_stacks",
        {
            "Stacks": [
                {
                    "StackName": "portfolio-agent-data",
                    "StackStatus": "CREATE_COMPLETE",
                    "CreationTime": "2026-01-01T00:00:00Z",
                    "Outputs": [{"OutputKey": "ContentBucketName", "OutputValue": "bucket-1"}],
                }
            ]
        },
        {"StackName": "portfolio-agent-data"},
    )
    stubber.activate()

    with pytest.raises(LookupError, match="KnowledgeBaseId"):
        resolve_stack_outputs(client, "portfolio-agent-data", ["ContentBucketName", "KnowledgeBaseId"])
    stubber.deactivate()


# --- resolve_data_source_id --------------------------------------------------


def test_resolve_data_source_id_returns_matching_id() -> None:
    client = boto3.client("bedrock-agent", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x")
    stubber = Stubber(client)
    stubber.add_response(
        "list_data_sources",
        {
            "dataSourceSummaries": [
                {
                    "dataSourceId": "ds-1",
                    "knowledgeBaseId": "kb-1",
                    "name": "portfolio-agent-content",
                    "status": "AVAILABLE",
                    "updatedAt": "2026-01-01T00:00:00Z",
                }
            ]
        },
        {"knowledgeBaseId": "kb-1"},
    )
    stubber.activate()

    data_source_id = resolve_data_source_id(client, "kb-1", "portfolio-agent-content")

    assert data_source_id == "ds-1"
    stubber.deactivate()


def test_resolve_data_source_id_raises_when_not_exactly_one_match() -> None:
    client = boto3.client("bedrock-agent", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x")
    stubber = Stubber(client)
    stubber.add_response(
        "list_data_sources",
        {"dataSourceSummaries": []},
        {"knowledgeBaseId": "kb-1"},
    )
    stubber.activate()

    with pytest.raises(LookupError, match="exactly one"):
        resolve_data_source_id(client, "kb-1", "portfolio-agent-content")
    stubber.deactivate()


# --- upload_files -------------------------------------------------------------


def test_upload_files_puts_each_file_with_content_prefixed_key(tmp_path: Path) -> None:
    (tmp_path / "cv").mkdir()
    (tmp_path / "portfolio" / "en").mkdir(parents=True)
    pdf = tmp_path / "cv" / "resume.pdf"
    md = tmp_path / "portfolio" / "en" / "about.md"
    pdf.write_bytes(b"%PDF-1.4")
    md.write_text("# About")
    files = [pdf, md]
    client = _FakeS3Client()

    keys = upload_files(client, "bucket-1", tmp_path, files)

    assert keys == ["content/cv/resume.pdf", "content/portfolio/en/about.md"]
    assert client.calls[0]["Bucket"] == "bucket-1"
    assert client.calls[0]["Key"] == "content/cv/resume.pdf"
    assert client.calls[0]["Body"] == b"%PDF-1.4"


def test_upload_files_sets_content_type_from_extension(tmp_path: Path) -> None:
    (tmp_path / "cv").mkdir()
    (tmp_path / "portfolio" / "en").mkdir(parents=True)
    pdf = tmp_path / "cv" / "resume.pdf"
    md = tmp_path / "portfolio" / "en" / "about.md"
    pdf.write_bytes(b"%PDF-1.4")
    md.write_text("# About")
    client = _FakeS3Client()

    upload_files(client, "bucket-1", tmp_path, [pdf, md])

    assert client.calls[0]["ContentType"] == "application/pdf"
    assert client.calls[1]["ContentType"] == "text/markdown"


# --- start_and_wait_for_ingestion ---------------------------------------------


def test_start_and_wait_for_ingestion_polls_until_complete() -> None:
    client = _FakeBedrockAgentClient(
        [
            _job("STARTING"),
            _job("IN_PROGRESS"),
            _job("COMPLETE", statistics={"numberOfNewDocumentsIndexed": 2}),
        ]
    )
    sleeps: list[float] = []

    result = start_and_wait_for_ingestion(
        client,
        "kb-1",
        "ds-1",
        sleep_fn=sleeps.append,
        poll_interval_seconds=5.0,
        timeout_seconds=60.0,
        now_fn=iter([0.0, 1.0, 2.0, 3.0]).__next__,
    )

    assert isinstance(result, IngestionResult)
    assert result.status == "COMPLETE"
    assert result.ingestion_job_id == "job-1"
    assert result.statistics == {"numberOfNewDocumentsIndexed": 2}
    assert len(client.poll_calls) == 2
    assert sleeps == [5.0, 5.0]


def test_start_and_wait_for_ingestion_returns_failed_with_reasons() -> None:
    client = _FakeBedrockAgentClient([_job("STARTING"), _job("FAILED", failureReasons=["bad document"])])

    result = start_and_wait_for_ingestion(
        client,
        "kb-1",
        "ds-1",
        sleep_fn=lambda _seconds: None,
        poll_interval_seconds=5.0,
        timeout_seconds=60.0,
        now_fn=iter([0.0, 1.0, 2.0]).__next__,
    )

    assert result.status == "FAILED"
    assert result.failure_reasons == ["bad document"]


def test_start_and_wait_for_ingestion_raises_on_timeout() -> None:
    client = _FakeBedrockAgentClient([_job("STARTING"), _job("IN_PROGRESS"), _job("IN_PROGRESS")])

    with pytest.raises(IngestionTimeoutError, match="job-1"):
        start_and_wait_for_ingestion(
            client,
            "kb-1",
            "ds-1",
            sleep_fn=lambda _seconds: None,
            poll_interval_seconds=5.0,
            timeout_seconds=10.0,
            now_fn=iter([0.0, 20.0]).__next__,
        )


def test_start_and_wait_for_ingestion_wire_contract_via_stubber() -> None:
    """Verifies request/response shapes against the real `bedrock-agent`
    service model (parameter names, required fields) — the hand-written fake
    above only proves the polling loop's control flow."""
    client = boto3.client("bedrock-agent", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x")
    stubber = Stubber(client)
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    stubber.add_response(
        "start_ingestion_job",
        {"ingestionJob": {**_job("STARTING"), "startedAt": started_at, "updatedAt": started_at}},
        {"knowledgeBaseId": "kb-1", "dataSourceId": "ds-1"},
    )
    stubber.add_response(
        "get_ingestion_job",
        {"ingestionJob": {**_job("COMPLETE"), "startedAt": started_at, "updatedAt": started_at}},
        {"knowledgeBaseId": "kb-1", "dataSourceId": "ds-1", "ingestionJobId": "job-1"},
    )
    stubber.activate()

    result = start_and_wait_for_ingestion(
        client, "kb-1", "ds-1", sleep_fn=lambda _seconds: None, poll_interval_seconds=1.0, timeout_seconds=30.0
    )

    assert result.status == "COMPLETE"
    stubber.deactivate()


# --- run_sync ------------------------------------------------------------------


def test_run_sync_returns_1_on_validation_error(tmp_path: Path) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = run_sync(
        content_dir=tmp_path,
        s3_client=_FakeS3Client(),
        bedrock_agent_client=_FakeBedrockAgentClient([_job("COMPLETE")]),
        bucket_name="bucket-1",
        knowledge_base_id="kb-1",
        data_source_id="ds-1",
        sleep_fn=lambda _seconds: None,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert "empty" in stderr.getvalue()


def test_run_sync_returns_0_on_complete_and_prints_summary(tmp_path: Path) -> None:
    (tmp_path / "cv").mkdir()
    (tmp_path / "cv" / "resume.pdf").write_bytes(b"%PDF-1.4")
    stdout = io.StringIO()
    s3_client = _FakeS3Client()

    exit_code = run_sync(
        content_dir=tmp_path,
        s3_client=s3_client,
        bedrock_agent_client=_FakeBedrockAgentClient([_job("STARTING"), _job("COMPLETE")]),
        bucket_name="bucket-1",
        knowledge_base_id="kb-1",
        data_source_id="ds-1",
        sleep_fn=lambda _seconds: None,
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert len(s3_client.calls) == 1
    assert "Uploaded 1 file" in stdout.getvalue()
    assert "COMPLETE" in stdout.getvalue()


def test_run_sync_dry_run_discovers_and_prints_keys_without_uploading(tmp_path: Path) -> None:
    (tmp_path / "cv").mkdir()
    (tmp_path / "cv" / "resume.pdf").write_bytes(b"%PDF-1.4")
    stdout = io.StringIO()
    s3_client = _FakeS3Client()
    bedrock_client = _FakeBedrockAgentClient([_job("COMPLETE")])

    exit_code = run_sync(
        content_dir=tmp_path,
        s3_client=s3_client,
        bedrock_agent_client=bedrock_client,
        bucket_name="bucket-1",
        knowledge_base_id="kb-1",
        data_source_id="ds-1",
        dry_run=True,
        sleep_fn=lambda _seconds: None,
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert s3_client.calls == []
    assert bedrock_client.start_calls == []
    assert "content/cv/resume.pdf" in stdout.getvalue()


def test_run_sync_returns_1_on_failed_status(tmp_path: Path) -> None:
    (tmp_path / "cv").mkdir()
    (tmp_path / "cv" / "resume.pdf").write_bytes(b"%PDF-1.4")
    stderr = io.StringIO()

    exit_code = run_sync(
        content_dir=tmp_path,
        s3_client=_FakeS3Client(),
        bedrock_agent_client=_FakeBedrockAgentClient([_job("STARTING"), _job("FAILED", failureReasons=["bad"])]),
        bucket_name="bucket-1",
        knowledge_base_id="kb-1",
        data_source_id="ds-1",
        sleep_fn=lambda _seconds: None,
        stdout=io.StringIO(),
        stderr=stderr,
    )

    assert exit_code == 1
    assert "bad" in stderr.getvalue()
