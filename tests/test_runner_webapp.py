"""Runner support for web-app (template) targets: the config passthrough that
lets a chat.z.ai-style target actually be probed, plus the commercial/auth gate
that keeps it OFF until an operator authorizes it."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))        # lib/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "runner"))
import run  # noqa: E402


WEBAPP = {
    "name": "chat-z-ai-webapp", "kind": "webapp", "base_url": "https://chat.z.ai",
    "api_style": "template", "cookie_env": "ZAI_COOKIE",
    "chat_path": "/api/paas/v4/chat/completions", "models_path": "/api/paas/v4/models",
    "request_template": {"model": "glm-4.6",
                         "messages": [{"role": "user", "content": "__PROMPT__"}]},
    "response_text_path": "choices.0.message.content",
    "response_prompt_tokens_path": "usage.prompt_tokens",
    "authorized": False, "public": False,
}


def test_write_probe_config_passes_webapp_fields(tmp_path):
    p = tmp_path / "cfg.json"
    run.write_probe_config(WEBAPP, str(p))
    cfg = json.load(open(p))[0]
    assert cfg["api_style"] == "template"
    assert cfg["cookie_env"] == "ZAI_COOKIE"
    assert cfg["chat_path"] == "/api/paas/v4/chat/completions"
    assert cfg["request_template"]["messages"][0]["content"] == "__PROMPT__"
    assert cfg["response_text_path"] == "choices.0.message.content"
    assert cfg["response_prompt_tokens_path"] == "usage.prompt_tokens"


def test_openai_target_keeps_engine_defaults(tmp_path):
    # a plain API target must NOT get web-app keys (would override engine defaults)
    p = tmp_path / "cfg.json"
    run.write_probe_config(
        {"name": "x", "base_url": "https://api.x/v1", "api_style": "openai", "auth_env": "K"},
        str(p))
    cfg = json.load(open(p))[0]
    assert "request_template" not in cfg and "cookie_env" not in cfg
    assert cfg["auth_value_env"] == "K"


def test_webapp_skipped_when_commercial_gate_off(monkeypatch):
    monkeypatch.delenv("OBSERVATORY_PROBE_COMMERCIAL", raising=False)
    ok, why = run.should_probe(WEBAPP)
    assert not ok and "commercial gate off" in why


def test_webapp_needs_authorization_even_with_gate_on(monkeypatch):
    monkeypatch.setenv("OBSERVATORY_PROBE_COMMERCIAL", "1")
    ok, why = run.should_probe(WEBAPP)                       # authorized: False
    assert not ok and "authorized=false" in why
    ok2, _ = run.should_probe({**WEBAPP, "authorized": True})
    assert ok2                                               # both gates satisfied
