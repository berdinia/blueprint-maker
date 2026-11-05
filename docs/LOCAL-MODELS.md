# Local models

The framework talks to any local server that implements either

* the OpenAI-compatible chat API (`POST /v1/chat/completions`, images as
  `data:` URLs; e.g. llama.cpp server, LM Studio, vLLM, an Ollama
  OpenAI-compatible route), or
* the native Ollama chat API (`POST /api/chat`, base64 `images`).

Pick one with `inference.primary.api`. A base URL ending in `/v1` is handled.

## Two models

* `models.extract` — a vision model that transcribes drawings into the
  observation schema (`extraction-response`).
* `models.review` — a **different** vision model that reviews presentation of
  the rendered PNG (`review-response`). Configuration rejects identical names.

Both are heavy jobs: the cross-process lock guarantees only one runs at a time
on the machine, regardless of how many shells or launchers are used.

## Prompts

Prompt templates live in `src/blueprint_pipeline/extraction/prompts.py` and
contain placeholders only. Each template has an ID and a content hash; editing
a prompt makes existing extractions stale (they are re-run on `resume`).

The extraction prompt asks for verbatim `raw_text` plus a separate
`normalized` reading, and tells the model to leave `source_id` null. If a model
still returns a `source_id`, it is recorded as `model_reported_source_id`,
compared, and replaced by the register's ID.

## Output handling

| Model behaviour | Outcome |
|---|---|
| `<think>…</think>` before the JSON | removed, noted |
| JSON in one surrounding code fence | fence removed, noted |
| prose before/after JSON | `PARSE_FAILED` |
| JSON that violates the schema | `SCHEMA_FAILED` |
| `legible: true` with no observations | `SEMANTIC_FAILED` |
| HTTP 200, empty content | `EMPTY_RESPONSE` |
| stream ends without terminal event | `STREAM_INCOMPLETE` |
| `finish_reason: length` / server error event | `MODEL_ABORTED` |

## Probing

```bash
blueprint --project <dir> config --probe
```

sends only a model-listing request (`/v1/models` or `/api/tags`), never evidence.

## Practical notes (Apple Silicon)

* Keep `inference.concurrency: 1`; unified memory is shared between models.
* Large vision models can take minutes per page: raise
  `inference.request_timeout_s` rather than enabling retries — a timed-out
  request is never retried automatically.
* Reduce `inference.max_image_px` if the model's context overflows
  (`MODEL_ABORTED` with `finish_reason=length`).
