"""Typed, validated configuration.

Precedence (lowest → highest): built-in defaults → YAML file → ``BLUEPRINT_*``
environment variables → explicit overrides passed by the caller.

Guarantees:

* Local-only by default (``privacy.mode = loopback``): every endpoint must be a
  loopback address. ``private_network`` additionally permits RFC 1918 / ULA /
  link-local literals and hostnames listed explicitly in ``privacy.allowed_hosts``.
  There is no mode that permits public hosts.
* No cloud provider and no implicit fallback. Falling back from the primary to
  the native endpoint happens only when ``inference.fallback_to_native`` is true,
  only before any request body has been sent, and it is logged.
* Unknown keys, legacy variable names and invalid values raise
  :class:`~blueprint_pipeline.errors.ConfigError` before any inference starts.
* Secrets are never stored: an API key is referenced by the *name* of an
  environment variable (``api_key_env``) and read at request time.
"""

from __future__ import annotations

import dataclasses
import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml

from ..errors import ConfigError, PrivacyPolicyError
from ..io.atomic import canonical_json, sha256_bytes

PrivacyMode = Literal["loopback", "private_network"]
ApiFlavor = Literal["openai", "ollama"]

CONFIG_SCHEMA_VERSION = 1

# Environment variables read by the loader: name -> dotted settings key.
ENV_VARS: dict[str, str] = {
    "BLUEPRINT_PROJECT_ROOT": "paths.project_root",
    "BLUEPRINT_INPUT_DIR": "paths.input_dir",
    "BLUEPRINT_WORK_DIR": "paths.work_dir",
    "BLUEPRINT_RESULTS_DIR": "paths.results_dir",
    "BLUEPRINT_PRIVACY_MODE": "privacy.mode",
    "BLUEPRINT_ENDPOINT_URL": "inference.primary.url",
    "BLUEPRINT_ENDPOINT_API": "inference.primary.api",
    "BLUEPRINT_ENDPOINT_API_KEY_ENV": "inference.primary.api_key_env",
    "BLUEPRINT_NATIVE_ENDPOINT_URL": "inference.native.url",
    "BLUEPRINT_ENDPOINT_FALLBACK": "inference.fallback_to_native",
    "BLUEPRINT_EXTRACT_MODEL": "models.extract",
    "BLUEPRINT_REVIEW_MODEL": "models.review",
    "BLUEPRINT_REQUEST_TIMEOUT_S": "inference.request_timeout_s",
    "BLUEPRINT_CONNECT_TIMEOUT_S": "inference.connect_timeout_s",
    "BLUEPRINT_CONTEXT_SIZE": "inference.context_size",
    "BLUEPRINT_MAX_OUTPUT_TOKENS": "inference.max_output_tokens",
    "BLUEPRINT_MAX_IMAGE_PX": "inference.max_image_px",
    "BLUEPRINT_MAX_IMAGE_BYTES": "inference.max_image_bytes",
    "BLUEPRINT_CONCURRENCY": "inference.concurrency",
    "BLUEPRINT_STREAM": "inference.stream",
    "BLUEPRINT_RETRY_MAX_ATTEMPTS": "retry.max_attempts",
    "BLUEPRINT_LOCK_WAIT_S": "lock.wait_s",
}

# Names used by the previous scaffold. They are rejected (not ignored) so a
# stale environment cannot silently configure something unexpected.
LEGACY_ENV_VARS: dict[str, str] = {
    "BLUEPRINT_NATIVE_ENDPOINT": "BLUEPRINT_NATIVE_ENDPOINT_URL",
    "BLUEPRINT_PROXY_ENDPOINT": "BLUEPRINT_ENDPOINT_URL",
    "BLUEPRINT_OUTPUT_DIR": "BLUEPRINT_RESULTS_DIR",
    "BLUEPRINT_RENDER_DIR": "BLUEPRINT_WORK_DIR (renders live under <work>/render)",
    "BLUEPRINT_MODEL_DIR": "BLUEPRINT_WORK_DIR (model lives under <work>/canonical)",
    "BLUEPRINT_REGISTRY": "<project>/register/source-register.json",
    "BLUEPRINT_LOCK_PATH": "<work>/locks/heavy-job.lock",
    "BLUEPRINT_PAGE_W_MM": "render.page_w_mm in the YAML config",
    "BLUEPRINT_PAGE_H_MM": "render.page_h_mm in the YAML config",
    "BLUEPRINT_MARGIN_MM": "render.margin_mm in the YAML config",
    "BLUEPRINT_TITLE_H_MM": "render.title_h_mm in the YAML config",
    "BLUEPRINT_NOTES_H_MM": "render.side_w_mm in the YAML config",
    "BLUEPRINT_GAP_MM": "(removed)",
    "RENDER_PAGE_W_MM": "render.page_w_mm in the YAML config",
    "RENDER_PAGE_H_MM": "render.page_h_mm in the YAML config",
}


@dataclass(frozen=True)
class EndpointSettings:
    url: str
    api: ApiFlavor = "openai"
    api_key_env: str | None = None


@dataclass(frozen=True)
class InferenceSettings:
    primary: EndpointSettings | None = None
    native: EndpointSettings | None = None
    fallback_to_native: bool = False
    request_timeout_s: float = 900.0
    connect_timeout_s: float = 5.0
    context_size: int = 8192
    max_output_tokens: int = 4096
    max_image_px: int = 2048
    max_image_bytes: int = 12_000_000
    concurrency: int = 1
    stream: bool = True
    temperature: float = 0.0


@dataclass(frozen=True)
class ModelSettings:
    extract: str | None = None
    review: str | None = None


@dataclass(frozen=True)
class RetrySettings:
    # Total attempts including the first. Retries happen only for failures that
    # occur before the server could have started work (connection refused) or
    # explicit "busy" answers (HTTP 429/503). Timeouts after the request body was
    # sent are never retried automatically: the model may still be running.
    max_attempts: int = 2
    backoff_s: float = 2.0


@dataclass(frozen=True)
class PrivacySettings:
    mode: PrivacyMode = "loopback"
    allowed_hosts: tuple[str, ...] = ()


@dataclass(frozen=True)
class LockSettings:
    wait_s: float = 0.0
    poll_s: float = 1.0


@dataclass(frozen=True)
class RenderSettings:
    page_w_mm: float = 420.0
    page_h_mm: float = 297.0
    margin_mm: float = 10.0
    title_h_mm: float = 24.0
    side_w_mm: float = 110.0
    dim_band_mm: float = 12.0
    label_h_mm: float = 3.5
    min_label_h_mm: float = 2.0
    note_h_mm: float = 2.6
    png_dpi: int = 150
    coordinate_decimals: int = 3


@dataclass(frozen=True)
class ReviewSettings:
    max_repair_cycles: int = 2


@dataclass(frozen=True)
class PathSettings:
    project_root: Path
    input_dir: Path
    work_dir: Path
    results_dir: Path

    @property
    def register_dir(self) -> Path:
        return self.project_root / "register"

    @property
    def lock_path(self) -> Path:
        return self.work_dir / "locks" / "heavy-job.lock"


@dataclass(frozen=True)
class Settings:
    paths: PathSettings
    privacy: PrivacySettings = field(default_factory=PrivacySettings)
    inference: InferenceSettings = field(default_factory=InferenceSettings)
    models: ModelSettings = field(default_factory=ModelSettings)
    retry: RetrySettings = field(default_factory=RetrySettings)
    lock: LockSettings = field(default_factory=LockSettings)
    render: RenderSettings = field(default_factory=RenderSettings)
    review: ReviewSettings = field(default_factory=ReviewSettings)

    # -- derived ---------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["paths"] = {k: str(v) for k, v in d["paths"].items()}
        d["privacy"]["allowed_hosts"] = list(self.privacy.allowed_hosts)
        return d

    def redacted_dict(self) -> dict[str, Any]:
        """Settings safe to log. No secrets are stored, but URLs are reduced to
        scheme://host:port and env-var *names* only are shown."""
        d = self.to_dict()
        for key in ("primary", "native"):
            ep = d["inference"].get(key)
            if ep:
                parts = urlsplit(ep["url"])
                ep["url"] = f"{parts.scheme}://{parts.hostname}:{parts.port or ''}".rstrip(":")
        return d

    def config_hash(self) -> str:
        """Hash of every setting that can change an artifact's content.

        Paths are excluded so a project can be moved without invalidating work;
        endpoint URLs are excluded because the *model identity* (recorded
        separately) is what matters for derived content.
        """
        d = self.to_dict()
        d.pop("paths")
        d.pop("lock")
        d["inference"].pop("primary", None)
        d["inference"].pop("native", None)
        d["inference"].pop("request_timeout_s", None)
        d["inference"].pop("connect_timeout_s", None)
        d["inference"].pop("fallback_to_native", None)
        d.pop("retry")
        return sha256_bytes(canonical_json(d).encode())

    def render_hash(self) -> str:
        return sha256_bytes(canonical_json(dataclasses.asdict(self.render)).encode())

    # -- inference readiness ------------------------------------------------
    def require_inference(self, role: Literal["extract", "review"]) -> tuple[EndpointSettings, str]:
        """Return (endpoint, model) for ``role`` or raise ConfigError.

        Called before any lock is taken or request is built.
        """
        ep = self.inference.primary
        if ep is None:
            raise ConfigError(
                "no inference endpoint configured: set inference.primary.url "
                "(or BLUEPRINT_ENDPOINT_URL) to a local endpoint"
            )
        model = self.models.extract if role == "extract" else self.models.review
        if not model:
            var = "BLUEPRINT_EXTRACT_MODEL" if role == "extract" else "BLUEPRINT_REVIEW_MODEL"
            raise ConfigError(f"no {role} model configured: set models.{role} (or {var})")
        validate_endpoint(ep.url, self.privacy)
        return ep, model


# ---------------------------------------------------------------------------
# Endpoint privacy validation
# ---------------------------------------------------------------------------
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8",  # RFC 1918
        "172.16.0.0/12",  # RFC 1918
        "192.168.0.0/16",  # RFC 1918
        "169.254.0.0/16",  # IPv4 link-local
        "fc00::/7",  # IPv6 unique local
        "fe80::/10",  # IPv6 link-local
    )
)


def _host_is_permitted(host: str, privacy: PrivacySettings) -> tuple[bool, str]:
    h = host.strip("[]").lower()
    if h == "localhost":
        return True, "loopback hostname"
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        if privacy.mode == "private_network" and h in {a.lower() for a in privacy.allowed_hosts}:
            return True, "hostname explicitly allowed"
        return False, (
            "hostnames other than 'localhost' are not resolved or trusted; use a literal "
            "address, or list the hostname in privacy.allowed_hosts with mode private_network"
        )
    if ip.is_loopback:
        return True, "loopback address"
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return False, "unspecified/multicast/reserved address"
    if privacy.mode == "private_network":
        # Explicit ranges: ``ipaddress.is_private`` also covers documentation and
        # benchmarking ranges, which must not be treated as a trusted local network.
        if any(ip in net for net in PRIVATE_NETWORKS if net.version == ip.version):
            return True, "private-network address"
        if h in {a.lower() for a in privacy.allowed_hosts}:
            return True, "address explicitly allowed"
        return False, "address is not loopback, private, or explicitly allowed"
    return False, "privacy mode 'loopback' permits loopback addresses only"


def validate_endpoint(url: str, privacy: PrivacySettings) -> None:
    """Raise PrivacyPolicyError unless ``url`` is a permitted local endpoint."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise PrivacyPolicyError(f"endpoint scheme must be http or https, got {parts.scheme!r}")
    if parts.username or parts.password:
        raise PrivacyPolicyError(
            "credentials embedded in an endpoint URL are not allowed; use api_key_env"
        )
    if parts.query or parts.fragment:
        raise PrivacyPolicyError("endpoint URL must not contain a query string or fragment")
    if not parts.hostname:
        raise PrivacyPolicyError("endpoint URL has no host")
    try:
        _ = parts.port
    except ValueError as exc:
        raise PrivacyPolicyError(f"invalid endpoint port: {exc}") from exc
    ok, why = _host_is_permitted(parts.hostname, privacy)
    if not ok:
        raise PrivacyPolicyError(f"endpoint host {parts.hostname!r} rejected: {why}")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
_TOP_KEYS = {
    "schema_version",
    "paths",
    "privacy",
    "inference",
    "models",
    "retry",
    "lock",
    "render",
    "review",
}


def _set_dotted(d: dict[str, Any], dotted: str, value: Any) -> None:
    cur = d
    parts = dotted.split(".")
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _coerce(value: Any, typ: Any, key: str) -> Any:
    origin = getattr(typ, "__origin__", None)
    try:
        if typ is bool or typ == "bool":
            if isinstance(value, bool):
                return value
            s = str(value).strip().lower()
            if s in ("1", "true", "yes", "on"):
                return True
            if s in ("0", "false", "no", "off"):
                return False
            raise ValueError(f"not a boolean: {value!r}")
        if typ is int or typ == "int":
            if isinstance(value, bool):
                raise ValueError("boolean given")
            if isinstance(value, float) and not value.is_integer():
                raise ValueError("not an integer")
            return int(value)
        if typ is float or typ == "float":
            if isinstance(value, bool):
                raise ValueError("boolean given")
            f = float(value)
            if f != f or f in (float("inf"), float("-inf")):
                raise ValueError("not finite")
            return f
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{key}: invalid value {value!r} ({exc})") from exc
    del origin
    return value


def _build(cls: Any, data: Mapping[str, Any] | None, prefix: str) -> Any:
    data = dict(data or {})
    fields = {f.name: f for f in dataclasses.fields(cls)}
    unknown = sorted(set(data) - set(fields))
    if unknown:
        raise ConfigError(f"unknown configuration key(s) under {prefix or 'root'}: {unknown}")
    kwargs: dict[str, Any] = {}
    for name, f in fields.items():
        if name not in data:
            continue
        v = data[name]
        key = f"{prefix}.{name}" if prefix else name
        t = f.type if isinstance(f.type, str) else getattr(f.type, "__name__", str(f.type))
        if t in ("bool", "int", "float"):
            kwargs[name] = _coerce(v, t, key)
        elif t.startswith("EndpointSettings"):
            if v is None:
                kwargs[name] = None
            elif isinstance(v, Mapping):
                if not v.get("url"):
                    kwargs[name] = None
                else:
                    kwargs[name] = _build(EndpointSettings, v, key)
            else:
                raise ConfigError(f"{key}: expected a mapping with 'url'")
        elif t.startswith("tuple"):
            if v is None:
                v = []
            if not isinstance(v, (list, tuple)):
                raise ConfigError(f"{key}: expected a list")
            kwargs[name] = tuple(str(x) for x in v)
        else:
            kwargs[name] = None if v is None else str(v) if not isinstance(v, str) else v
    return cls(**kwargs)


def _validate(s: Settings) -> None:
    errs: list[str] = []
    inf = s.inference
    if s.privacy.mode not in ("loopback", "private_network"):
        errs.append(f"privacy.mode must be 'loopback' or 'private_network', got {s.privacy.mode!r}")
    if s.privacy.mode == "loopback" and s.privacy.allowed_hosts:
        errs.append("privacy.allowed_hosts is only valid with privacy.mode=private_network")
    for label, ep in (("inference.primary", inf.primary), ("inference.native", inf.native)):
        if ep is None:
            continue
        if ep.api not in ("openai", "ollama"):
            errs.append(f"{label}.api must be 'openai' or 'ollama', got {ep.api!r}")
        try:
            validate_endpoint(ep.url, s.privacy)
        except PrivacyPolicyError as exc:
            errs.append(f"{label}.url: {exc}")
        if ep.api_key_env is not None and not ep.api_key_env.replace("_", "").isalnum():
            errs.append(f"{label}.api_key_env must be an environment variable name")
    if inf.fallback_to_native and inf.native is None:
        errs.append("inference.fallback_to_native is true but inference.native is not configured")
    if inf.concurrency != 1:
        errs.append(
            "inference.concurrency must be 1: one heavy local-model job runs at a time "
            "(enforced across processes by the heavy-job lock)"
        )
    for name in ("request_timeout_s", "connect_timeout_s"):
        if not 0 < getattr(inf, name) <= 86_400:
            errs.append(f"inference.{name} must be in (0, 86400]")
    for name, lo, hi in (
        ("context_size", 512, 1_048_576),
        ("max_output_tokens", 16, 262_144),
        ("max_image_px", 64, 16_384),
        ("max_image_bytes", 1024, 200_000_000),
    ):
        v = getattr(inf, name)
        if not lo <= v <= hi:
            errs.append(f"inference.{name}={v} outside [{lo}, {hi}]")
    if not 0.0 <= inf.temperature <= 2.0:
        errs.append("inference.temperature must be within [0, 2]")
    if not 1 <= s.retry.max_attempts <= 5:
        errs.append("retry.max_attempts must be within [1, 5]")
    if s.retry.backoff_s < 0:
        errs.append("retry.backoff_s must be >= 0")
    if s.lock.wait_s < 0 or s.lock.poll_s <= 0:
        errs.append("lock.wait_s must be >= 0 and lock.poll_s > 0")
    if s.models.extract and s.models.review and s.models.extract.strip() == s.models.review.strip():
        errs.append("models.review must differ from models.extract (independent visual review)")
    if not 0 <= s.review.max_repair_cycles <= 2:
        errs.append("review.max_repair_cycles must be within [0, 2]")
    r = s.render
    if r.page_w_mm <= 0 or r.page_h_mm <= 0:
        errs.append("render page size must be positive")
    geo_w = r.page_w_mm - 2 * r.margin_mm - r.side_w_mm - 2 * r.dim_band_mm
    geo_h = r.page_h_mm - 2 * r.margin_mm - r.title_h_mm - 2 * r.dim_band_mm
    if geo_w < 20 or geo_h < 20:
        errs.append(
            f"render regions leave no room for geometry ({geo_w:.1f} x {geo_h:.1f} mm); "
            "reduce margins, side column, title or dimension band"
        )
    if not 0 < r.min_label_h_mm <= r.label_h_mm:
        errs.append("render.min_label_h_mm must be in (0, label_h_mm]")
    if not 50 <= r.png_dpi <= 600:
        errs.append("render.png_dpi must be within [50, 600]")
    if not 0 <= r.coordinate_decimals <= 6:
        errs.append("render.coordinate_decimals must be within [0, 6]")
    if errs:
        raise ConfigError("invalid configuration:\n  - " + "\n  - ".join(errs))


def load_settings(
    config_path: str | os.PathLike[str] | None = None,
    *,
    project_root: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Settings:
    """Load and validate settings. Raises ConfigError on any problem."""
    env = os.environ if env is None else env
    legacy = sorted(k for k in LEGACY_ENV_VARS if env.get(k))
    if legacy:
        lines = [f"{k} -> {LEGACY_ENV_VARS[k]}" for k in legacy]
        raise ConfigError(
            "legacy environment variable(s) set; they are no longer read. Replace:\n  "
            + "\n  ".join(lines)
        )

    raw: dict[str, Any] = {}
    base_dir = Path.cwd()
    if config_path is None and env.get("BLUEPRINT_CONFIG"):
        config_path = env["BLUEPRINT_CONFIG"]
    if config_path is not None:
        cp = Path(config_path)
        if not cp.is_file():
            raise ConfigError(f"config file not found: {cp}")
        try:
            loaded = yaml.safe_load(cp.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"config file is not valid YAML: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ConfigError("config file must contain a mapping at top level")
        raw = loaded
        base_dir = cp.resolve().parent
    unknown = sorted(set(raw) - _TOP_KEYS)
    if unknown:
        raise ConfigError(f"unknown top-level configuration key(s): {unknown}")
    sv = raw.pop("schema_version", CONFIG_SCHEMA_VERSION)
    if sv != CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"config schema_version {sv!r} is not supported (expected {CONFIG_SCHEMA_VERSION})"
        )

    for var, dotted in ENV_VARS.items():
        if env.get(var) not in (None, ""):
            _set_dotted(raw, dotted, env[var])
    for dotted, value in (overrides or {}).items():
        _set_dotted(raw, dotted, value)
    if project_root is not None:
        _set_dotted(raw, "paths.project_root", str(project_root))

    p = dict(raw.get("paths") or {})
    unknown_p = sorted(set(p) - {"project_root", "input_dir", "work_dir", "results_dir"})
    if unknown_p:
        raise ConfigError(f"unknown configuration key(s) under paths: {unknown_p}")
    root = Path(p.get("project_root") or ".")
    if not root.is_absolute():
        root = base_dir / root
    root = root.resolve()

    def _dir(key: str, default: str) -> Path:
        v = Path(p.get(key) or default)
        return (v if v.is_absolute() else root / v).resolve()

    paths = PathSettings(
        project_root=root,
        input_dir=_dir("input_dir", "input"),
        work_dir=_dir("work_dir", "work"),
        results_dir=_dir("results_dir", "results"),
    )
    if paths.input_dir in (paths.work_dir, paths.results_dir) or paths.input_dir in (
        paths.work_dir.parents
    ):
        raise ConfigError("input_dir must be separate from work_dir and results_dir")

    settings = Settings(
        paths=paths,
        privacy=_build(PrivacySettings, raw.get("privacy"), "privacy"),
        inference=_build(InferenceSettings, raw.get("inference"), "inference"),
        models=_build(ModelSettings, raw.get("models"), "models"),
        retry=_build(RetrySettings, raw.get("retry"), "retry"),
        lock=_build(LockSettings, raw.get("lock"), "lock"),
        render=_build(RenderSettings, raw.get("render"), "render"),
        review=_build(ReviewSettings, raw.get("review"), "review"),
    )
    _validate(settings)
    return settings
