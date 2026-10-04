from pathlib import Path

from research_automation.config import load_settings


def test_load_settings_uses_project_relative_database(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("RESEARCH_DB_PATH", raising=False)
    settings = load_settings(tmp_path)
    assert settings.database_path == tmp_path / "data/research.db"


def test_key_status_never_returns_secret_values(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MERITZ_APP_KEY", "top-secret")
    settings = load_settings(tmp_path)
    assert settings.key_status()["MERITZ_APP_KEY"] is True
    assert "top-secret" not in repr(settings.key_status())


def test_load_settings_uses_local_ollama_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    settings = load_settings(tmp_path)
    assert settings.ollama_model == "qwen3.5:9b"
    assert settings.ollama_base_url == "http://127.0.0.1:11434"
