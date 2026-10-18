"""Keep test runs offline and away from the user's private settings/workspace."""
from pathlib import Path

import httpx
import pytest


@pytest.fixture(autouse=True)
def isolated_offline_environment(tmp_path: Path, monkeypatch):
    from app.core import llm_config
    from app.web import server
    from app.web.session import AppSession

    monkeypatch.setattr(llm_config, "CONFIG_PATH", tmp_path / ".ai-settings.private.json")
    monkeypatch.setattr(server, "CONFIG_PATH", llm_config.CONFIG_PATH)
    for name in ("AIPC_LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY",
                 "AIPC_LLM_BASE_URL", "OPENAI_BASE_URL", "AIPC_LLM_MODEL", "OPENAI_MODEL",
                 "AIPC_LLM_TIMEOUT", "AIPC_LLM_MAX_IMAGES", "AIPC_IMAGE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(server, "SESSION", AppSession(workspace_path=tmp_path / "workspace"))

    original_send = httpx.Client.send
    def offline_send(client, request, *args, **kwargs):
        if request.url.host not in {"testserver", "localhost"} or client.__class__.__name__ != "TestClient":
            raise AssertionError("Tests must mock external HTTP; actual network is forbidden")
        return original_send(client, request, *args, **kwargs)
    monkeypatch.setattr(httpx.Client, "send", offline_send)
