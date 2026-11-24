from app.config import load_settings

KEYS = ("AI_GATEWAY_API_KEY", "TYPESAFE_API_KEY", "ANTHROPIC_API_KEY")


def _clear_env(monkeypatch):
    for k in KEYS:
        monkeypatch.setenv(k, "x")  # records the original state so it is restored after the test
        monkeypatch.delenv(k)


def test_dotenv_file_in_working_directory_is_loaded(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    (tmp_path / ".env").write_text("AI_GATEWAY_API_KEY=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    s = load_settings()
    assert s.gateway_api_key == "from-dotenv"
    assert s.jev_configured


def test_real_environment_beats_dotenv_file(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    (tmp_path / ".env").write_text("AI_GATEWAY_API_KEY=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "from-shell")
    assert load_settings().gateway_api_key == "from-shell"


def test_no_keys_means_mock_mode(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.chdir(tmp_path)  # no .env here
    assert not load_settings().jev_configured