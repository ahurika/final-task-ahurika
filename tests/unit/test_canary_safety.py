"""
Unit Tests for Gemini Canary Safety & Isolation Invariants
Location: tests/unit/test_canary_safety.py

Phase 3E.6.1: Deterministic offline verification of canary runner safety:
1. Never print or partially mask the API key.
2. Confirm missing credentials fail fast before SDK or network initialization.
3. Confirm canary makes at most one HTTP/API attempt on failure (max_retries=0).
4. Confirm exceptions and logs sanitize credentials.
5. Confirm fixed baseline parameters (gemini-embedding-001, 768 dim, RETRIEVAL_DOCUMENT, public sentence).
6. Confirm no database writes, corpus processing, or persistence dependencies.
7. Confirm normal provider retry behavior is preserved (default max_retries=3).
"""

import ast
import inspect
import os
import pytest
from typing import Any, List

from scripts import canary_gemini
from scripts.canary_gemini import (
    CANARY_TEXT,
    CANARY_TITLE,
    _sanitize_output,
    load_env_file,
    run_canary,
)
from src.embeddings.config import (
    MODEL_GEMINI_EMBEDDING_001,
    PROVISIONAL_DEFAULT_DIMENSION,
)
from src.embeddings.gemini_provider import (
    GeminiEmbeddingProvider,
    TASK_TYPE_DOCUMENT,
)
from src.embeddings.retry import RetryPolicy


# =============================================================================
# Mock SDK Client Helpers for Offline Deterministic Testing
# =============================================================================

class MockEmbeddingValues:
    def __init__(self, values: List[float]):
        self.values = values


class MockEmbedContentResponse:
    def __init__(self, values: List[float]):
        self.embedding = MockEmbeddingValues(values)
        self.embeddings = [self.embedding]


class MockGenAIModels:
    def __init__(self, return_values: List[float]):
        self.return_values = return_values
        self.call_count = 0
        self.last_kwargs = {}

    def embed_content(self, **kwargs):
        self.call_count += 1
        self.last_kwargs = kwargs
        return MockEmbedContentResponse(self.return_values)


class MockGenAIClient:
    def __init__(self, return_values: List[float]):
        self.models = MockGenAIModels(return_values)


class FailingGenAIModels:
    def __init__(self, exception_to_raise: Exception):
        self.exception_to_raise = exception_to_raise
        self.call_count = 0

    def embed_content(self, **kwargs):
        self.call_count += 1
        raise self.exception_to_raise


class FailingGenAIClient:
    def __init__(self, exception_to_raise: Exception):
        self.models = FailingGenAIModels(exception_to_raise)


# =============================================================================
# Test Suite: Canary Runner Safety & Offline Validation
# =============================================================================

class TestCanarySafety:
    """Verifies all safety guarantees for the controlled Gemini canary probe."""

    @pytest.fixture(autouse=True)
    def isolate_canary_env(self, monkeypatch, tmp_path):
        """
        Isolates test runs from the workspace root .env file.
        Redirects default .env resolution to a non-existent file in tmp_path,
        ensuring unit tests run in a strictly controlled offline environment.
        """
        empty_env = tmp_path / "test_isolated.env"
        monkeypatch.setattr(canary_gemini, "DEFAULT_DOTENV_PATH", str(empty_env))

    def test_canary_fails_fast_when_api_key_unset(self, monkeypatch, capsys):
        """Missing GEMINI_API_KEY must fail immediately before any network or SDK activity."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            run_canary(exit_on_error=True)

        assert exc_info.value.code == 1
        captured = capsys.readouterr().out
        assert "[ERROR] GEMINI_API_KEY environment variable is not set." in captured
        assert "$env:GEMINI_API_KEY" in captured

    def test_canary_fails_fast_when_api_key_empty_or_whitespace(self, monkeypatch, capsys):
        """Whitespace-only GEMINI_API_KEY must also fail fast."""
        monkeypatch.setenv("GEMINI_API_KEY", "   \t\n  ")

        with pytest.raises(SystemExit) as exc_info:
            run_canary(exit_on_error=True)

        assert exc_info.value.code == 1
        captured = capsys.readouterr().out
        assert "[ERROR] GEMINI_API_KEY environment variable is not set." in captured

    def test_canary_never_prints_or_partially_masks_key(self, monkeypatch, capsys):
        """The API key must NEVER be printed, even partially masked (no prefix/suffix)."""
        secret_key = "AIzaSyTestSecretKeyNeverPrintThisUnderAnyCircumstances12345"
        monkeypatch.setenv("GEMINI_API_KEY", secret_key)

        mock_vector = [0.01 * (i % 10) for i in range(PROVISIONAL_DEFAULT_DIMENSION)]
        mock_client = MockGenAIClient(mock_vector)

        vector = run_canary(client_override=mock_client, exit_on_error=False)

        captured = capsys.readouterr().out

        # Absolute secret confidentiality invariants:
        assert secret_key not in captured, "Full secret key found in canary output!"
        assert "AIzaSy" not in captured, "Key prefix was leaked in canary output!"
        assert "12345" not in captured, "Key suffix was leaked in canary output!"
        assert "..." not in captured.split("API Key Status")[1].split("\n")[0], "Partial masking was used!"

        # Correct reporting invariant:
        assert f"Length: {len(secret_key)} chars, value strictly hidden" in captured
        assert len(vector) == PROVISIONAL_DEFAULT_DIMENSION

    def test_canary_disables_retries_at_most_one_attempt_on_transient_failure(self, monkeypatch, capsys):
        """
        When the canary call fails (even on transient HTTP 429), it must make
        at most ONE attempt without retrying.
        """
        secret_key = "AIzaSyTestTransientSecret"
        monkeypatch.setenv("GEMINI_API_KEY", secret_key)

        failing_client = FailingGenAIClient(
            Exception("HTTP 429 RESOURCE_EXHAUSTED: Rate limit exceeded retry in 10s")
        )

        with pytest.raises(SystemExit) as exc_info:
            run_canary(client_override=failing_client, exit_on_error=True)

        assert exc_info.value.code == 1
        assert failing_client.models.call_count == 1, (
            f"Expected at most 1 attempt, but got {failing_client.models.call_count} calls!"
        )

        captured = capsys.readouterr().out
        assert "Total API attempts made: 1 (max allowed: 1)" in captured
        assert "[CANARY FAILED during API execution]" in captured

    def test_canary_disables_retries_on_503_transient_failure(self, monkeypatch, capsys):
        """When canary encounters transient HTTP 503, it must make at most ONE attempt and abort."""
        secret_key = "AIzaSyTest503CanarySecret"
        monkeypatch.setenv("GEMINI_API_KEY", secret_key)

        failing_client = FailingGenAIClient(
            Exception("503 Service Unavailable: overloaded backend")
        )

        with pytest.raises(SystemExit) as exc_info:
            run_canary(client_override=failing_client, exit_on_error=True)

        assert exc_info.value.code == 1
        assert failing_client.models.call_count == 1
        captured = capsys.readouterr().out
        assert "Total API attempts made: 1 (max allowed: 1)" in captured

    def test_canary_sanitizes_exceptions_containing_secret(self, monkeypatch, capsys):
        """If an external exception contains the secret key in URL or text, it must be redacted."""
        secret_key = "AIzaSyTestSecretKey1234567890ABCDEF"
        monkeypatch.setenv("GEMINI_API_KEY", secret_key)

        leaky_message = (
            f"Connection error to endpoint "
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent?key={secret_key}"
        )
        leaky_client = FailingGenAIClient(Exception(leaky_message))

        with pytest.raises(SystemExit) as exc_info:
            run_canary(client_override=leaky_client, exit_on_error=True)

        assert exc_info.value.code == 1
        captured = capsys.readouterr().out

        # Invariant: Secret key string must not appear anywhere in captured output
        assert secret_key not in captured, "Secret key was leaked via exception message!"
        assert "[REDACTED_API_KEY]" in captured or "key=[REDACTED]" in captured

    def test_canary_parameters_and_isolation(self, monkeypatch):
        """Verifies model, dimensions, task type, input sentence, and independence."""
        secret_key = "AIzaSyTestSecretParams"
        monkeypatch.setenv("GEMINI_API_KEY", secret_key)

        mock_vector = [0.05] * PROVISIONAL_DEFAULT_DIMENSION
        mock_client = MockGenAIClient(mock_vector)

        run_canary(client_override=mock_client, exit_on_error=False)

        assert mock_client.models.call_count == 1
        kwargs = mock_client.models.last_kwargs

        # Model check
        assert kwargs["model"] == MODEL_GEMINI_EMBEDDING_001
        assert kwargs["model"] == "gemini-embedding-001"

        # Content check
        assert kwargs["contents"] == CANARY_TEXT
        assert "PostgreSQL provides a number of configuration parameters" in kwargs["contents"]

        # Config check
        cfg = kwargs["config"]
        assert cfg["output_dimensionality"] == 768
        assert cfg["task_type"] == "RETRIEVAL_DOCUMENT"
        assert cfg["title"] == "Chapter 20. Server Configuration"

    def test_canary_script_has_no_database_or_corpus_dependencies(self):
        """Confirms canary script does not import database modules or corpus processing logic."""
        script_path = os.path.abspath(canary_gemini.__file__)
        with open(script_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=script_path)

        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_modules.add(node.module)

        forbidden_prefixes = ("src.db", "psycopg2", "asyncpg", "sqlalchemy", "neon")
        for mod in imported_modules:
            for forbidden in forbidden_prefixes:
                assert not mod.startswith(forbidden), (
                    f"Canary script improperly imports forbidden module: {mod}"
                )

    def test_default_gemini_provider_retains_normal_retries(self):
        """Disabling retries for canary must NOT alter the default provider retry policy."""
        provider = GeminiEmbeddingProvider(
            model_name=MODEL_GEMINI_EMBEDDING_001,
            dimensions=PROVISIONAL_DEFAULT_DIMENSION,
            is_live_enabled=False,
        )
        assert provider._retry_policy.max_retries == 3, (
            "Default GeminiEmbeddingProvider max_retries should remain 3"
        )

    def test_provider_safe_repr_never_leaks_api_key(self):
        """GeminiEmbeddingProvider.__repr__ must never expose the raw API key."""
        secret_key = "AIzaSySuperSecretProviderKey123"
        provider = GeminiEmbeddingProvider(
            api_key=secret_key,
            is_live_enabled=False,
        )
        repr_str = repr(provider)
        assert secret_key not in repr_str
        assert "has_api_key=True" in repr_str

    def test_sanitize_output_utility(self):
        """Verifies _sanitize_output redacts explicit keys and regex patterns."""
        secret = "CustomSecretKey999"
        text = f"Error with key={secret} and key=AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz1234567"
        sanitized = _sanitize_output(text, secret)
        assert secret not in sanitized
        assert "AIzaSy" not in sanitized
        assert "[REDACTED_API_KEY]" in sanitized or "key=[REDACTED]" in sanitized

    def test_canary_loads_api_key_from_dotenv_file(self, monkeypatch, tmp_path, capsys):
        """Proves canary loads GEMINI_API_KEY from .env when process env is unset."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        test_secret = "AIzaSyTestDotEnvKeyLoading123456789"
        custom_env = tmp_path / ".env.test"
        custom_env.write_text(f"GEMINI_API_KEY={test_secret}\n", encoding="utf-8")

        mock_vector = [0.01] * PROVISIONAL_DEFAULT_DIMENSION
        mock_client = MockGenAIClient(mock_vector)

        vector = run_canary(
            client_override=mock_client,
            exit_on_error=False,
            dotenv_path=str(custom_env),
        )

        assert len(vector) == PROVISIONAL_DEFAULT_DIMENSION
        assert mock_client.models.call_count == 1
        captured = capsys.readouterr().out
        assert f"Length: {len(test_secret)} chars, value strictly hidden" in captured
        assert test_secret not in captured

    def test_canary_env_var_takes_precedence_over_dotenv(self, monkeypatch, tmp_path):
        """Process environment variable must take precedence over .env file (override=False)."""
        active_env_key = "AIzaSyActiveProcessEnvKeyMustPrevail"
        dotenv_key = "AIzaSyDotEnvKeyShouldBeIgnored"
        monkeypatch.setenv("GEMINI_API_KEY", active_env_key)

        custom_env = tmp_path / ".env.precedence"
        custom_env.write_text(f"GEMINI_API_KEY={dotenv_key}\n", encoding="utf-8")

        mock_vector = [0.02] * PROVISIONAL_DEFAULT_DIMENSION
        mock_client = MockGenAIClient(mock_vector)

        run_canary(
            client_override=mock_client,
            exit_on_error=False,
            dotenv_path=str(custom_env),
        )

        assert os.environ["GEMINI_API_KEY"] == active_env_key

    def test_canary_fails_fast_when_dotenv_key_is_empty(self, monkeypatch, tmp_path, capsys):
        """Empty or whitespace GEMINI_API_KEY in .env must fail fast."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        custom_env = tmp_path / ".env.empty_val"
        custom_env.write_text("GEMINI_API_KEY=   \t  \n", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            run_canary(exit_on_error=True, dotenv_path=str(custom_env))

        assert exc_info.value.code == 1
        captured = capsys.readouterr().out
        assert "[ERROR] GEMINI_API_KEY environment variable is not set." in captured

    def test_canary_fails_fast_when_dotenv_missing_key(self, monkeypatch, tmp_path, capsys):
        """.env file present without GEMINI_API_KEY must fail fast."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        custom_env = tmp_path / ".env.other"
        custom_env.write_text("OTHER_VAR=some_value\nDATABASE_URL=postgres://...\n", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            run_canary(exit_on_error=True, dotenv_path=str(custom_env))

        assert exc_info.value.code == 1
        captured = capsys.readouterr().out
        assert "[ERROR] GEMINI_API_KEY environment variable is not set." in captured

    def test_canary_handles_missing_dotenv_file_gracefully(self, monkeypatch, tmp_path, capsys):
        """Non-existent .env file falls through to fail-fast missing key error."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        missing_env = tmp_path / "nonexistent.env"

        with pytest.raises(SystemExit) as exc_info:
            run_canary(exit_on_error=True, dotenv_path=str(missing_env))

        assert exc_info.value.code == 1
        captured = capsys.readouterr().out
        assert "[ERROR] GEMINI_API_KEY environment variable is not set." in captured

    def test_load_env_file_pure_python_fallback_parser(self, monkeypatch, tmp_path):
        """Verifies pure-Python fallback parser handles comments, quotes, and whitespace."""
        # Force fallback parser by making dotenv import fail
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "dotenv":
                raise ImportError("Mocked missing python-dotenv")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        monkeypatch.delenv("PARSER_TEST_UNQUOTED", raising=False)
        monkeypatch.delenv("PARSER_TEST_DOUBLE_QUOTED", raising=False)
        monkeypatch.delenv("PARSER_TEST_SINGLE_QUOTED", raising=False)
        monkeypatch.delenv("PARSER_TEST_HAS_EQUALS", raising=False)
        monkeypatch.delenv("PARSER_TEST_EXISTING", raising=False)
        monkeypatch.setenv("PARSER_TEST_EXISTING", "original_value")

        env_file = tmp_path / ".env.fallback_test"
        env_file.write_text(
            "# Leading comment\n"
            "\n"
            "PARSER_TEST_UNQUOTED=simple_value\n"
            'PARSER_TEST_DOUBLE_QUOTED="quoted value with spaces"\n'
            "PARSER_TEST_SINGLE_QUOTED='single quoted value'\n"
            "PARSER_TEST_HAS_EQUALS=part1=part2=part3\n"
            "PARSER_TEST_EXISTING=new_value_should_be_ignored\n",
            encoding="utf-8",
        )

        loaded = load_env_file(dotenv_path=str(env_file), override=False)
        assert loaded is True
        assert os.environ.get("PARSER_TEST_UNQUOTED") == "simple_value"
        assert os.environ.get("PARSER_TEST_DOUBLE_QUOTED") == "quoted value with spaces"
        assert os.environ.get("PARSER_TEST_SINGLE_QUOTED") == "single quoted value"
        assert os.environ.get("PARSER_TEST_HAS_EQUALS") == "part1=part2=part3"
        assert os.environ.get("PARSER_TEST_EXISTING") == "original_value"
