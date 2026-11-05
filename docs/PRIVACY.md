# Privacy

The framework is built to run against private evidence on a local machine.

## Guarantees enforced in code

| Concern | Control | Verified by |
|---|---|---|
| Evidence leaving the machine/network | endpoints must be loopback (default) or explicitly private; no public mode; checked at load and before each connection | `tests/unit/test_config.py` |
| Ambient proxy settings rerouting traffic | inference uses `http.client`, which ignores `HTTP(S)_PROXY` | `test_proxy_environment_is_ignored` |
| Silent fallback | fallback only with `fallback_to_native: true`, only to a validated local endpoint, logged | `test_no_fallback_unless_explicitly_enabled` |
| New network code sneaking in | `scripts/audit_network_usage.py` fails on any networking import outside the allowlist or any non-loopback URL literal; runs in tests and CI | `tests/unit/test_network_audit.py` |
| Image metadata (EXIF GPS, device, XMP) reaching the model | normalization copies pixels only | normalization code + synthetic run |
| Secrets in config/logs/artifacts | keys referenced by env-var *name*; log fields with secret-like names redacted | `test_api_key_read_from_env_by_name_only` |
| Absolute paths / usernames in artifacts | artifacts reference paths relative to the work dir; synthetic run scans outputs | `no_absolute_paths_in_artifacts` check |
| Evidence modification | inventory and normalize re-hash inputs; mismatch blocks | `test_output_input_hash_mismatch_detected` |

Not stored: the base64 image in request logs (only its SHA-256).
Stored locally: raw model responses (needed for audit) — they contain
transcriptions of your evidence and live under `work/`, which is git-ignored.

The heavy-job lock record contains the hostname and PID; it stays in
`work/locks/` and is not copied into the review packet.

## Repository hygiene

* Only synthetic fixtures belong in this repository. They are marked
  `SYNTHETIC TEST DATA — NOT DERIVED FROM A REAL BUILDING` and use `SYN-*`
  IDs and abstract labels (ALPHA, BRAVO, …).
* `work/`, `results/`, `input/` and `register/` of real projects must live
  outside the repository; `.gitignore` excludes the common names.
* Before exporting anything, run the private-term scanner with your private
  dictionary (never committed):

```bash
python scripts/check_local_only.py <export-dir> --terms <private-terms.json>
```

It reports file, line and term index only — never the matched private text.

## Receiving changes from outside

Before merging code produced elsewhere:

```bash
python scripts/audit_network_usage.py src
```

```bash
python -m pytest -q
```

```bash
blueprint run-synthetic
```

then review the diff of `src/blueprint_pipeline/inference/` and
`config/settings.py` by hand.
