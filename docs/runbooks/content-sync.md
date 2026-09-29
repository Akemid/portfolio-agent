# Content sync runbook

How to add or update the CV/portfolio content the agent answers from.

## Quick path

1. Lay out your content locally, mirroring the S3 structure.
2. Preview with `--dry-run`.
3. Run the real sync.
4. Confirm the new content is retrievable.

### 1. Local content layout

`scripts/sync_content.py --content-dir <dir>` uploads every file under
`<dir>` to `content/<relative path>` in the content bucket. The Knowledge
Base's S3 data source only ingests paths under `content/`, and the layout
convention is fixed:

```
<dir>/
  cv/
    resume.pdf
  portfolio/
    en/
      about.md
      project-x.md
    es/
      about.md
      project-x.md
```

Only PDF and Markdown files are expected. `discover_content_files` rejects
symlinks and skips hidden files/directories (`.git/`, `.DS_Store`, ...) so a
stray dotfile in your content directory never gets uploaded.

### 2. Preview with `--dry-run`

```bash
uv run python scripts/sync_content.py --content-dir <dir> --dry-run
```

Prints the planned S3 keys and exits — no upload, no ingestion job, no AWS
call at all. Confirm the file count and paths look right before the real run.

### 3. Run the real sync

```bash
uv run python scripts/sync_content.py --content-dir <dir>
```

This uploads every discovered file, then starts a Knowledge Base ingestion
job and polls it to a terminal state (`COMPLETE`, `FAILED`, `STOPPED`, or a
timeout). Exits non-zero on any failure. Add `--verbose` to see the full
`s3://bucket/key` for each upload and where bucket/Knowledge Base/data-source
ids were resolved from (CLI override vs. stack output).

Bucket, Knowledge Base id, and data source id resolve automatically from the
`portfolio-agent-data` stack's outputs and `ListDataSources` — override with
`--bucket-name`, `--knowledge-base-id`, or `--data-source-id` only if you
need to point at a non-default deployment.

### 4. What happens during re-index

Ingestion re-embeds and re-indexes the changed content in place. In-flight
questions are unaffected: the Knowledge Base continues serving retrieval
against the previous index until the new one is ready, so there is no read
outage. There is no way to target a re-index at only the changed files —
`StartIngestionJob` re-processes the whole data source each time.

Avoid running a sync while demonstrating the bot live: a question answered
mid-ingestion may retrieve from a partially updated index.

### 5. Confirm the new content is retrievable

Ask the deployed bot a question that only the new content can answer:

```bash
uv run python tests/smoke/test_smoke.py --expect-substring "<a fact only in the new content>"
```

This asserts the substring appears in a real answer, proving the agent is
grounded on the content you just uploaded, not just responding generically.

## Checklist

- [ ] Content directory mirrors `content/cv/` and `content/portfolio/{en,es}/`
- [ ] `--dry-run` output matches what you expect to upload
- [ ] Real sync exits 0 (ingestion reached `COMPLETE`)
- [ ] `--expect-substring` smoke test confirms the new content is retrievable

## Next step

Need to undo a bad sync or a bad deploy? See [`rollback.md`](./rollback.md).
