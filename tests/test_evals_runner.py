"""Tests for the eval-runner backend factory and provenance collection.

These guard the publication-credibility contract:
- the CLI cannot silently fall back to mock when a live backend is asked
  for but misconfigured;
- every run records enough metadata (git sha, fingerprints, backend
  identity) to be reproducible.
"""

from __future__ import annotations

import pytest

from evals.backends import (
    DEFAULT_MINIMAX_BASE_URL,
    DEFAULT_MINIMAX_MODEL,
    BackendSpec,
    MissingBackendConfigError,
    build_backend_factory,
    spec_from_args,
)
from evals.provenance import (
    FINGERPRINT_FILES,
    _repo_root,
    build_provenance,
    collect_git_info,
    fingerprint_files,
)


class TestBackendFactory:
    def test_mock_has_no_required_config(self):
        factory = build_backend_factory(BackendSpec(type="mock"))
        backend = factory()
        # MockLLMBackend exposes a generate() method
        assert hasattr(backend, "generate")

    def test_minimax_without_api_key_fails_loud(self):
        with pytest.raises(MissingBackendConfigError, match="API key"):
            build_backend_factory(BackendSpec(type="minimax"))

    def test_minimax_resolves_default_model_and_base_url(self):
        spec = BackendSpec(type="minimax", api_key="fake")
        assert spec.resolved_model == DEFAULT_MINIMAX_MODEL
        assert spec.resolved_base_url == DEFAULT_MINIMAX_BASE_URL
        # Factory builds without hitting the network
        factory = build_backend_factory(spec)
        backend = factory()
        assert hasattr(backend, "generate")
        backend.close()

    def test_provider_needs_base_url(self):
        with pytest.raises(MissingBackendConfigError, match="--base-url"):
            build_backend_factory(
                BackendSpec(type="provider", requested_model="qwen")
            )

    def test_provider_needs_model(self):
        with pytest.raises(MissingBackendConfigError, match="--model"):
            build_backend_factory(
                BackendSpec(type="provider", base_url="https://provider.example/v1")
            )

    def test_openai_compat_needs_base_url(self):
        with pytest.raises(MissingBackendConfigError, match="--base-url"):
            build_backend_factory(
                BackendSpec(type="openai_compat", requested_model="qwen")
            )

    def test_openai_compat_needs_model(self):
        with pytest.raises(MissingBackendConfigError, match="--model"):
            build_backend_factory(
                BackendSpec(type="openai_compat", base_url="http://localhost:8080/v1")
            )

    def test_unknown_backend_type_fails_loud(self):
        with pytest.raises(MissingBackendConfigError, match="Unknown backend"):
            build_backend_factory(BackendSpec(type="bedrock"))

    def test_factory_returns_fresh_instance_per_call(self):
        factory = build_backend_factory(BackendSpec(type="mock"))
        a = factory()
        b = factory()
        assert a is not b


class TestSpecFromArgs:
    def test_env_var_resolution(self, monkeypatch):
        monkeypatch.setenv("MY_CUSTOM_KEY", "sk-xyz")
        spec = spec_from_args(
            backend="minimax",
            model="",
            base_url="",
            api_key="",
            api_key_env="MY_CUSTOM_KEY",
        )
        assert spec.api_key == "sk-xyz"

    def test_explicit_api_key_wins(self, monkeypatch):
        monkeypatch.setenv("MY_CUSTOM_KEY", "from-env")
        spec = spec_from_args(
            backend="minimax",
            model="",
            base_url="",
            api_key="from-arg",
            api_key_env="MY_CUSTOM_KEY",
        )
        assert spec.api_key == "from-arg"

    def test_fallback_env_vars_for_minimax(self, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.setenv("MINIMAX_API_KEY", "mm-key")
        spec = spec_from_args(
            backend="minimax",
            model="",
            base_url="",
            api_key="",
            api_key_env="",
        )
        assert spec.api_key == "mm-key"

    def test_provider_falls_back_to_llm_api_key(self, monkeypatch):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        monkeypatch.setenv("LLM_API_KEY", "generic-key")
        spec = spec_from_args(
            backend="provider",
            model="demo-model",
            base_url="https://provider.example/v1",
            api_key="",
            api_key_env="",
        )
        assert spec.api_key == "generic-key"


class TestProvenance:
    def test_git_info_returns_real_sha(self):
        sha, branch, _dirty = collect_git_info()
        # This repo is a git checkout, so sha and branch must be populated
        assert len(sha) == 40
        assert all(c in "0123456789abcdef" for c in sha)
        assert branch  # non-empty

    def test_fingerprints_deterministic(self):
        a = fingerprint_files(FINGERPRINT_FILES)
        b = fingerprint_files(FINGERPRINT_FILES)
        assert a == b
        # Every hash is 64 hex chars
        for h in a.values():
            assert len(h) == 64
            assert all(c in "0123456789abcdef" for c in h)

    def test_fingerprint_skips_missing_files(self):
        result = fingerprint_files(["this/file/does/not/exist.md", "config/soul.yaml"])
        assert "this/file/does/not/exist.md" not in result
        assert "config/soul.yaml" in result

    def test_repo_root_detects_source_archive_without_git(self, tmp_path):
        source = tmp_path / "source"
        (source / "config").mkdir(parents=True)
        (source / "evals").mkdir()
        (source / "pyproject.toml").write_text('[project]\nname = "project-nur"\n', encoding="utf-8")
        (source / "config" / "soul.yaml").write_text("identity: test\n", encoding="utf-8")
        (source / "pipeline.py").write_text("# pipeline marker\n", encoding="utf-8")
        probe = source / "evals" / "provenance.py"
        probe.write_text("# probe\n", encoding="utf-8")

        assert _repo_root(probe) == str(source)

    def test_build_provenance_populates_known_fields(self):
        prov = build_provenance(
            backend_type="mock",
            requested_model="",
            resolved_model="",
            base_url="",
            scenario_set="phase11",
            scenario_ids=["a", "b", "c"],
        )
        assert prov.backend_type == "mock"
        assert prov.scenario_set == "phase11"
        assert prov.scenario_count == 3
        assert prov.scenario_ids == ["a", "b", "c"]
        assert prov.started_at  # ISO timestamp
        assert prov.host        # hostname
        assert len(prov.git_sha) == 40
        assert prov.config_fingerprints  # at least one file hashed
        # Execution counters default to zero — filled in after the run
        assert prov.llm_calls == 0
        # Unmeasured fields stay None (honest "not measured", not fake zero).
        # retries / prompt_tokens / completion_tokens need client-level
        # instrumentation to produce real values; until then they must
        # serialize as null, not 0.
        assert prov.temperature is None
        assert prov.max_tokens is None
        assert prov.retries is None
        assert prov.prompt_tokens is None
        assert prov.completion_tokens is None
        assert prov.estimated_cost_usd is None
        assert prov.failures == 0
