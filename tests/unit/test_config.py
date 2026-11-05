from __future__ import annotations

import pytest

from blueprint_pipeline.config.settings import PrivacySettings, load_settings, validate_endpoint
from blueprint_pipeline.errors import ConfigError, PrivacyPolicyError


def test_defaults_are_local_only_and_unconfigured(tmp_path):
    s = load_settings(project_root=tmp_path, env={})
    assert s.privacy.mode == "loopback"
    assert s.inference.primary is None and s.inference.fallback_to_native is False
    assert s.models.extract is None and s.models.review is None  # no hard-coded model names
    with pytest.raises(ConfigError, match="no inference endpoint"):
        s.require_inference("extract")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434",
        "http://localhost:8080/v1",
        "http://[::1]:9000",
        "https://127.0.0.2",
    ],
)
def test_loopback_endpoints_allowed(url):
    validate_endpoint(url, PrivacySettings())


@pytest.mark.parametrize(
    "url,why",
    [
        ("http://192.168.1.20:11434", "loopback addresses only"),
        ("https://api.example.com", "hostnames other than"),
        ("http://8.8.8.8", "loopback addresses only"),
        ("http://user:pw@127.0.0.1:1", "credentials"),
        ("http://127.0.0.1:1/?key=x", "query"),
        ("ftp://127.0.0.1", "scheme"),
        ("http://0.0.0.0:1", "unspecified"),
    ],
)
def test_non_local_endpoints_rejected(url, why):
    with pytest.raises(PrivacyPolicyError, match=why):
        validate_endpoint(url, PrivacySettings())


def test_private_network_mode():
    p = PrivacySettings(mode="private_network", allowed_hosts=("gpu-box.lan",))
    validate_endpoint("http://192.168.1.20:11434", p)
    validate_endpoint("http://10.0.0.5:11434", p)
    validate_endpoint("http://gpu-box.lan:11434", p)
    with pytest.raises(PrivacyPolicyError):
        validate_endpoint("http://203.0.113.9:11434", p)  # documentation range, not RFC 1918
    with pytest.raises(PrivacyPolicyError):
        validate_endpoint("http://100.64.0.1:11434", p)  # shared address space needs allowed_hosts
    with pytest.raises(PrivacyPolicyError):
        validate_endpoint("http://other.lan:11434", p)


def test_public_endpoint_fails_before_inference(tmp_path):
    with pytest.raises(ConfigError, match="rejected"):
        load_settings(
            project_root=tmp_path, env={"BLUEPRINT_ENDPOINT_URL": "https://cloud.example.com"}
        )


def test_legacy_env_vars_rejected_not_ignored(tmp_path):
    with pytest.raises(ConfigError, match="BLUEPRINT_PROXY_ENDPOINT -> BLUEPRINT_ENDPOINT_URL"):
        load_settings(project_root=tmp_path, env={"BLUEPRINT_PROXY_ENDPOINT": "http://127.0.0.1:1"})
    with pytest.raises(ConfigError, match="RENDER_PAGE_W_MM"):
        load_settings(project_root=tmp_path, env={"RENDER_PAGE_W_MM": "999"})


def test_explicit_dirs_are_not_suffixed(tmp_path):
    # Regression for D-C1: an explicit input dir must not gain an extra "/input".
    s = load_settings(
        project_root=tmp_path, env={"BLUEPRINT_INPUT_DIR": str(tmp_path / "evidence")}
    )
    assert s.paths.input_dir == (tmp_path / "evidence").resolve()
    assert s.paths.work_dir == (tmp_path / "work").resolve()


@pytest.mark.parametrize(
    "overrides,msg",
    [
        ({"inference.concurrency": 2}, "concurrency must be 1"),
        ({"models.extract": "m", "models.review": "m"}, "must differ"),
        ({"review.max_repair_cycles": 3}, "max_repair_cycles"),
        ({"inference.fallback_to_native": True}, "native is not configured"),
        ({"privacy.allowed_hosts": ["x.lan"]}, "only valid with"),
        ({"render.side_w_mm": 400}, "no room for geometry"),
        ({"inference.request_timeout_s": "abc"}, "invalid value"),
        ({"inference.max_image_px": 10}, "outside"),
    ],
)
def test_invalid_values_fail_early(tmp_path, overrides, msg):
    with pytest.raises(ConfigError, match=msg):
        load_settings(project_root=tmp_path, env={}, overrides=overrides)


def test_unknown_keys_rejected(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("schema_version: 1\ninference:\n  primery: {url: http://127.0.0.1:1}\n")
    with pytest.raises(ConfigError, match="unknown configuration key"):
        load_settings(cfg, env={})
    cfg.write_text("schema_version: 1\nclouds: true\n")
    with pytest.raises(ConfigError, match="unknown top-level"):
        load_settings(cfg, env={})


def test_yaml_then_env_precedence_and_relative_root(tmp_path):
    cfg = tmp_path / "proj" / "blueprint.yaml"
    cfg.parent.mkdir()
    cfg.write_text(
        "schema_version: 1\nmodels: {extract: from-yaml}\ninference:\n  primary: {url: 'http://127.0.0.1:1'}\n"
    )
    s = load_settings(cfg, env={"BLUEPRINT_EXTRACT_MODEL": "from-env"})
    assert s.models.extract == "from-env"
    assert s.paths.project_root == cfg.parent.resolve()


def test_secrets_never_stored_and_redaction(tmp_path):
    s = load_settings(
        project_root=tmp_path,
        env={},
        overrides={"inference.primary": {"url": "http://127.0.0.1:9/v1", "api_key_env": "SYN_KEY"}},
    )
    red = s.redacted_dict()
    assert red["inference"]["primary"]["url"] == "http://127.0.0.1:9"
    assert red["inference"]["primary"]["api_key_env"] == "SYN_KEY"  # the name, never a value


def test_config_hash_ignores_paths_but_not_models(tmp_path):
    a = load_settings(project_root=tmp_path / "a", env={}, overrides={"models.extract": "x"})
    b = load_settings(project_root=tmp_path / "b", env={}, overrides={"models.extract": "x"})
    c = load_settings(project_root=tmp_path / "a", env={}, overrides={"models.extract": "y"})
    assert a.config_hash() == b.config_hash() != c.config_hash()
