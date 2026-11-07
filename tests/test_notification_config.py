from pathlib import Path

from backend.app.services import notification_config


def test_read_env_file_ignores_comments_and_unquotes_values(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# ignored\nTELEGRAM_TOKEN='secret'\nTELEGRAM_CHAT_ID=1234\nINVALID\n",
        encoding="utf-8",
    )

    assert notification_config.read_env_file(env_file) == {
        "TELEGRAM_TOKEN": "secret",
        "TELEGRAM_CHAT_ID": "1234",
    }


def test_line_token_prefers_process_environment(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "from-environment")

    assert notification_config.line_access_token() == "from-environment"
