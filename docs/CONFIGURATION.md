# Configuration

Settings are typed and validated before any work starts. Sources, lowest to
highest precedence:

1. built-in defaults (local-only, no endpoint, no model);
2. a YAML file, found in this order: `--config FILE`, `BLUEPRINT_CONFIG`,
   `<--project>/blueprint.yaml`, `./blueprint.yaml` (see `config/example.local.yaml`);
3. `BLUEPRINT_*` environment variables (see `.env.example`);
4. `--project DIR` on the command line (sets `paths.project_root`).

Relative paths resolve against `paths.project_root`, which itself resolves
against the directory of the YAML file.

Inspect the resolved, redacted configuration and probe endpoints:

```bash
blueprint --config blueprint.yaml config --probe
```

## Keys

| Key | Default | Notes |
|---|---|---|
| `paths.project_root` | `.` | |
| `paths.input_dir` | `input` | must not contain `work_dir`/`results_dir` |
| `paths.work_dir` | `work` | |
| `paths.results_dir` | `results` | |
| `privacy.mode` | `loopback` | `loopback` or `private_network` |
| `privacy.allowed_hosts` | `[]` | hostnames/addresses allowed in `private_network` mode |
| `inference.primary.url` | none | required for extract/review |
| `inference.primary.api` | `openai` | `openai` → `/v1/chat/completions`; `ollama` → `/api/chat` |
| `inference.primary.api_key_env` | none | **name** of an env var holding a key |
| `inference.native` | none | optional second local endpoint |
| `inference.fallback_to_native` | `false` | explicit opt-in, see below |
| `inference.request_timeout_s` | `900` | per request, after connect |
| `inference.connect_timeout_s` | `5` | |
| `inference.context_size` | `8192` | sent as `num_ctx` to Ollama |
| `inference.max_output_tokens` | `4096` | `max_tokens` / `num_predict` |
| `inference.max_image_px` | `2048` | longest side after normalization |
| `inference.max_image_bytes` | `12000000` | encoded PNG limit |
| `inference.concurrency` | `1` | must be 1 |
| `inference.stream` | `true` | |
| `inference.temperature` | `0.0` | |
| `models.extract` / `models.review` | none | must differ from each other |
| `retry.max_attempts` | `2` | 1–5, see retry policy |
| `retry.backoff_s` | `2.0` | linear backoff |
| `lock.wait_s` / `lock.poll_s` | `0` / `1.0` | bounded wait for a busy lock |
| `render.*` | A3 landscape | page, margins, title, side column, dimension band, label sizes, DPI |
| `review.max_repair_cycles` | `2` | 0–2 |

Unknown keys, wrong types and out-of-range values raise a configuration error
(exit code 2). The legacy variable names from the previous scaffold
(`BLUEPRINT_PROXY_ENDPOINT`, `RENDER_PAGE_W_MM`, …) are rejected with the name
of their replacement rather than silently ignored.

## Privacy policy for endpoints

* `loopback` (default): `127.0.0.0/8`, `::1`, `localhost` only.
* `private_network`: additionally `10/8`, `172.16/12`, `192.168/16`,
  `169.254/16`, `fc00::/7`, `fe80::/10`, plus entries in `allowed_hosts`.
  Other hostnames are not resolved and are rejected unless listed.
* Never allowed: credentials in the URL, query strings, non-HTTP schemes,
  unspecified/multicast addresses. There is no mode that allows public hosts.

The URL is checked at load time and again before every connection.

## Fallback and retries

* Fallback from `primary` to `native` happens only if
  `inference.fallback_to_native: true`, only after a **connect** failure (no
  request body was sent), and it is recorded in the attempt log and in
  `model.fallback_used` of the extraction record.
* Retries happen only for connect failures and HTTP 429/503. A timeout or a
  dropped connection after the request was sent is **not** retried — the
  model may still be working on it.

## Secrets

No secret is ever stored in configuration, artifacts or logs. If your local
server needs a key, export it under any name and reference that name:

```bash
export LOCAL_LLM_KEY=...
```

```yaml
inference:
  primary: {url: http://127.0.0.1:8080/v1, api: openai, api_key_env: LOCAL_LLM_KEY}
```

## Configuration hash

`config_hash` covers every setting that can change derived content (models,
limits, render settings) and excludes paths, timeouts, retry and lock
settings. Artifacts derived under a different hash are reported stale.
