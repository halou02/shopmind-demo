"""连接探针的离线测试：全假客户端、全假响应，零真实请求。

沿用 tests/test_local_vision.py 的桩位（record_http 那套），因为 app.core.local_vision_client
与 app.core.local_vision_probe 里的 httpx 是同一个模块对象，挂一次就同时覆盖两层。
探针允许 GET（模型清单），而 record_http 只桩了 POST，所以本文件用 record_probe_http 包一层，
把 transport.get 也显式喂上——不去改那份已经被十几条测试依赖的公共夹具。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core import llm_config
from app.core import local_vision_probe as probe
from app.core.llm_config import LocalVisionConfig
from app.core.local_vision_probe import (PROBE_GENERATION_TIMEOUT_SECONDS,
                                         PROBE_SERVICE_TIMEOUT_SECONDS, PROBE_REASONS,
                                         REASON_ADDRESS_INVALID, REASON_ADDRESS_NOT_OLLAMA,
                                         REASON_BAD_ENVELOPE, REASON_MODEL_NOT_FOUND,
                                         REASON_NO_REPLY, REASON_NOT_TESTABLE, REASON_OUTPUT_TRUNCATED,
                                         REASON_PASSED, REASON_SERVICE_NOT_RUNNING, REASON_UNKNOWN,
                                         REASON_UPSTREAM_ERROR, STAGE_CONFIG, STAGE_GENERATION,
                                         STAGE_MODEL, STAGE_SERVICE, TAGS_ABSENT, TAGS_MATCHED,
                                         TAGS_NOT_JSON, TAGS_UNKNOWN_SHAPE, build_probe_body,
                                         classify_generation_body, classify_http_response,
                                         classify_tags_body, classify_tags_response,
                                         classify_transport_error, probe_endpoints, safe_json)
from tests.test_local_vision import (LOCAL_BASE, LOCAL_MODEL, TEXT_KEY, assert_no_secret,
                                     cloud_calls, enable_cloud_vision, enable_local_vision,
                                     enable_text, local_calls, local_config, ollama_payload,
                                     ollama_response, record_http, sent_body, valid_reply, v2_path)
from app.web import server

PROBE_URL = "/api/ai-capabilities/vision/test"
TEXT_PROBE_URL = "/api/ai-capabilities/text/test"
PROBE_ROUTES = {("post", PROBE_URL), ("post", TEXT_PROBE_URL)}
PAYLOAD_KEYS = ["checked", "message", "ok", "reason", "stage", "status_code"]

# 用来证明「不回显上游原文」的哨兵串：它只应出现在假响应体里，绝不该出现在任何结论里。
SENTINEL = "SENTINEL-UPSTREAM-TEXT-8731"


def messages() -> dict[str, str]:
    return probe._MESSAGES


def tags_payload(*names: str) -> dict:
    """真实 /api/tags 的形状：顶层 models，每项 name 与 model 同值（2026-10-14 本机实测 10/10）。"""
    return {"models": [{"name": name, "model": name, "size": 1, "digest": "d"} for name in names]}


def http_response(status_code: int, *, body: object = None, raw_text: str | None = None) -> Mock:
    """一份假响应。json 要么给得出，要么按本机实测那样抛 ValueError——绝不返回 Mock 对象。"""
    response = Mock(status_code=status_code)
    if body is not None:
        response.json = Mock(return_value=body)
        response.text = json.dumps(body, ensure_ascii=False)
    else:
        response.json = Mock(side_effect=ValueError(raw_text or "not json"))
        response.text = raw_text or "404 page not found"
    return response


# --- 1. 地址派生 ---

def test_tags_url_shares_the_validated_prefix_with_chat_url():
    chat, tags = probe_endpoints(LOCAL_BASE)
    assert (chat, tags) == ("http://127.0.0.1:11434/api/chat", "http://127.0.0.1:11434/api/tags")


def test_wrong_prefix_survives_into_both_urls_identically():
    """多粘一个 /v1 时两步都会撞 404，探针不会给出「一步对一步错」的误导结论。"""
    chat, tags = probe_endpoints(f"{LOCAL_BASE}/v1")
    assert (chat, tags) == ("http://127.0.0.1:11434/v1/api/chat",
                            "http://127.0.0.1:11434/v1/api/tags")


@pytest.mark.parametrize("suffix", ["/api/chat", "/api", "/api/api/chat", "/"])
def test_redundant_suffixes_are_collapsed_before_both_steps(suffix):
    chat, tags = probe_endpoints(LOCAL_BASE + suffix)
    assert chat == f"{LOCAL_BASE}/api/chat"
    assert tags == f"{LOCAL_BASE}/api/tags"


def test_literal_localhost_is_rewritten_to_the_loopback_ip_in_both_urls():
    chat, tags = probe_endpoints("http://localhost:11434")
    assert chat == "http://127.0.0.1:11434/api/chat"
    assert tags == "http://127.0.0.1:11434/api/tags"


# --- 2. 探针请求体不带图片，且沿用同一道引擎上限 ---

def test_probe_body_mirrors_the_real_shape_but_carries_no_image():
    body = build_probe_body(local_config())
    assert sorted(body) == ["format", "messages", "model", "options", "stream", "think"]
    assert body["options"] == {"num_ctx": 4096, "num_predict": 1200}
    assert len(body["messages"]) == 1 and body["messages"][0]["role"] == "user"
    assert "images" not in json.dumps(body, ensure_ascii=False)
    assert body["model"] == LOCAL_MODEL


# --- 3. 传输层异常：连接阶段失败必须归到「服务没开」 ---

@pytest.mark.parametrize("error,reason", [
    (httpx.ConnectError("boom"), REASON_SERVICE_NOT_RUNNING),
    (httpx.ConnectTimeout("boom"), REASON_SERVICE_NOT_RUNNING),
    (httpx.ReadTimeout("boom"), REASON_NO_REPLY),
    (httpx.WriteTimeout("boom"), REASON_NO_REPLY),
    (httpx.PoolTimeout("boom"), REASON_NO_REPLY),
    (httpx.ProtocolError("boom"), REASON_UPSTREAM_ERROR)])
def test_connect_phase_failures_are_not_reported_as_slow_model(error, reason):
    # httpx 0.28.1 实测 ConnectTimeout 不继承 ConnectError，所以它是被显式点名的，不是顺带命中的。
    assert classify_transport_error(error) == reason


# --- 4. 同一个 404 的两种真实形状，只能靠正文分开 ---

@pytest.mark.parametrize("status,body,tags_confirmed,reason", [
    # 本机实测：模型名不存在 → 404 + {"error":"model '…' not found"}
    (404, {"error": f"model '{SENTINEL}' not found"}, False, REASON_MODEL_NOT_FOUND),
    (404, {"error": f"model '{SENTINEL}' not found"}, True, REASON_MODEL_NOT_FOUND),
    # 本机实测：路径没挂对 → 404 + 纯文本 404 page not found（正文不是 JSON）
    (404, None, False, REASON_ADDRESS_NOT_OLLAMA),
    # 404 + JSON 但没有 error 字段（例如把 8080 粘成 Ollama 端口拿到的 {"detail":"Not Found"}）
    (404, {"detail": "Not Found"}, False, REASON_ADDRESS_NOT_OLLAMA),
    # 旁证生效：同一前缀的清单已经 200 出过，前缀就不可能是错的，只剩模型名
    (404, {"detail": "Not Found"}, True, REASON_MODEL_NOT_FOUND),
    (400, {"error": "bad request"}, False, REASON_UPSTREAM_ERROR),
    (500, None, True, REASON_UPSTREAM_ERROR),
    (503, None, False, REASON_UPSTREAM_ERROR),
    (301, None, True, REASON_ADDRESS_NOT_OLLAMA),
    (302, None, False, REASON_ADDRESS_NOT_OLLAMA),
    (307, None, False, REASON_ADDRESS_NOT_OLLAMA),
    (308, None, False, REASON_ADDRESS_NOT_OLLAMA),
    (200, {"done": True}, False, REASON_PASSED)])
def test_http_response_classification(status, body, tags_confirmed, reason):
    response = http_response(status, body=body)
    assert classify_http_response(response.status_code, safe_json(response),
                                  tags_confirmed=tags_confirmed) == reason


def test_error_field_must_be_non_empty_text_or_object():
    assert classify_http_response(404, {"error": "   "}, tags_confirmed=False) \
        == REASON_ADDRESS_NOT_OLLAMA
    assert classify_http_response(404, {"error": {"message": "x"}}, tags_confirmed=False) \
        == REASON_MODEL_NOT_FOUND
    assert classify_http_response(404, "not a dict", tags_confirmed=False) == REASON_ADDRESS_NOT_OLLAMA


def test_safe_json_never_touches_the_response_text():
    response = Mock(status_code=404)
    response.json = Mock(side_effect=ValueError(SENTINEL))
    response.text = SENTINEL
    assert safe_json(response) is None
    assert SENTINEL not in json.dumps(classify_http_response(404, safe_json(response),
                                                             tags_confirmed=False))


# --- 5. 模型清单判定：认不出来就下探，不臆断失败 ---

@pytest.mark.parametrize("body,expected", [
    (tags_payload(LOCAL_MODEL), TAGS_MATCHED),
    (tags_payload("other:1", LOCAL_MODEL), TAGS_MATCHED),
    ({"models": [{"name": LOCAL_MODEL}]}, TAGS_MATCHED),
    ({"tags": [{"model": LOCAL_MODEL}]}, TAGS_MATCHED),
    ({"models": [{"name": "foo"}]}, TAGS_ABSENT),
    ({"models": []}, TAGS_ABSENT),
    ({"models": [{"llamacpp": LOCAL_MODEL}]}, TAGS_ABSENT),
    ({"unexpected": 1}, TAGS_UNKNOWN_SHAPE),
    ({"models": ["plain-string"]}, TAGS_UNKNOWN_SHAPE),
    (["list-not-object"], TAGS_UNKNOWN_SHAPE),
    (None, TAGS_NOT_JSON)])
def test_tags_body_matching(body, expected):
    assert classify_tags_body(body, LOCAL_MODEL) == expected


def test_latest_suffix_is_treated_as_equivalent():
    assert classify_tags_body(tags_payload(f"{LOCAL_MODEL}:latest"), LOCAL_MODEL) == TAGS_MATCHED
    assert classify_tags_body(tags_payload(LOCAL_MODEL), f"{LOCAL_MODEL}:latest") == TAGS_MATCHED


@pytest.mark.parametrize("status,body,reason,confirmed", [
    (200, tags_payload(LOCAL_MODEL), REASON_PASSED, True),
    (200, {"unexpected": 1}, REASON_PASSED, False),
    (200, None, REASON_ADDRESS_NOT_OLLAMA, False),
    (404, None, REASON_ADDRESS_NOT_OLLAMA, False),
    (302, None, REASON_ADDRESS_NOT_OLLAMA, False),
    (500, None, REASON_UPSTREAM_ERROR, False)])
def test_service_step_verdicts(status, body, reason, confirmed):
    got, flag = classify_tags_response(status, body, LOCAL_MODEL)
    assert (got, flag) == (reason, confirmed)


def test_absent_model_is_decided_without_a_single_generation_request():
    got, flag = classify_tags_response(200, tags_payload("someone-elses-model:9"), LOCAL_MODEL)
    assert got == REASON_MODEL_NOT_FOUND and flag is False


# --- 6. 生成响应：判定顺序与业务层一致 ---

def test_envelope_order_not_done_outranks_truncation():
    assert classify_generation_body({"done": False, "done_reason": "length"}) == REASON_BAD_ENVELOPE
    assert classify_generation_body({"done": True, "done_reason": "length",
                                     "message": {"content": "x"}}) == REASON_OUTPUT_TRUNCATED
    assert classify_generation_body(ollama_payload(valid_reply())) == REASON_PASSED
    assert classify_generation_body({"done": True, "done_reason": "stop"}) == REASON_BAD_ENVELOPE
    assert classify_generation_body({"done": True, "done_reason": "stop",
                                     "message": {"content": "   "}}) == REASON_BAD_ENVELOPE
    assert classify_generation_body({"done": True, "done_reason": "unload",
                                     "message": {"content": "{}"}}) == REASON_PASSED
    assert classify_generation_body(None) == REASON_BAD_ENVELOPE


# --- 7. 话术表：四个目标原因各一句、互不相同，且不写秒数、不用禁词、不回显 ---

def test_every_reason_has_exactly_one_message_and_no_message_is_orphaned():
    assert set(messages()) == set(PROBE_REASONS)
    assert len(PROBE_REASONS) == 11


def test_the_four_target_reasons_read_as_four_different_sentences():
    four = [messages()[key] for key in (REASON_SERVICE_NOT_RUNNING, REASON_MODEL_NOT_FOUND,
                                        REASON_ADDRESS_NOT_OLLAMA, REASON_OUTPUT_TRUNCATED)]
    assert len(set(four)) == 4
    assert all(text and text != messages()[REASON_PASSED] for text in four)


@pytest.mark.parametrize("reason", PROBE_REASONS)
def test_message_is_one_plain_line_without_duration_or_banned_token(reason):
    text = messages()[reason]
    assert text.strip() == text and "\n" not in text
    # 不写死秒数：这台机器的冷载入耗时对队友的机器不成立
    assert not re.search(r"\d+\s*秒", text), text
    lowered = text.lower()
    for token in ("connected", "success", "verified", "reachable", "healthy"):
        assert token not in lowered, f"{reason} 的文案里有禁词 {token}"
    for token in (SENTINEL, ".private.json", "api_key"):
        assert token not in text


def test_probe_budgets_stay_under_the_frontend_default():
    assert PROBE_SERVICE_TIMEOUT_SECONDS + PROBE_GENERATION_TIMEOUT_SECONDS < 120


# --- 8. 结论载荷的形状：键固定、失败也是数据不是异常 ---

def test_payload_shape_is_closed_and_carries_no_upstream_text():
    outcome = probe.ProbeOutcome(False, REASON_MODEL_NOT_FOUND, STAGE_MODEL, 404)
    payload = outcome.payload({"base_url": LOCAL_BASE, "model": LOCAL_MODEL})
    assert sorted(payload) == ["checked", "message", "ok", "reason", "stage", "status_code"]
    assert payload["ok"] is False and payload["status_code"] == 404
    assert payload["checked"] == {"base_url": LOCAL_BASE, "model": LOCAL_MODEL}
    assert SENTINEL not in json.dumps(payload, ensure_ascii=False)


def test_unknown_reason_code_falls_back_instead_of_raising():
    outcome = probe.ProbeOutcome(False, "a_code_nobody_wrote_yet", STAGE_CONFIG)
    assert outcome.message() == messages()[REASON_UNKNOWN]
    assert set(outcome.payload()) == {"ok", "reason", "stage", "message", "status_code", "checked"}


@pytest.mark.parametrize("stage", [STAGE_CONFIG, STAGE_SERVICE, STAGE_MODEL, STAGE_GENERATION])
def test_stage_names_are_distinct_strings(stage):
    assert stage and isinstance(stage, str)


# --- 9. 端点层：桩位沿用 record_http，GET 那一步由本文件补上 ---

def record_probe_http(monkeypatch, *, tags: Mock | None = None, chat: Mock | None = None,
                      get_error: BaseException | None = None,
                      chat_error: BaseException | None = None):
    """record_http 只桩了 transport.post；探针第一步是 GET 模型清单，所以这里显式喂 get。

    默认形状取本机 2026-10-14 实测：/api/tags 返 200 且顶层是 models；生成那步返一个
    done_reason=stop 的完整信封。
    """
    factory, transport = record_http(monkeypatch, response=chat or ollama_response({"probe": "ok"}))
    if get_error is not None:
        transport.get.side_effect = get_error
    else:
        transport.get.return_value = tags if tags is not None else http_response(
            200, body=tags_payload(LOCAL_MODEL))
    if chat_error is not None:
        transport.post.side_effect = chat_error
    return factory, transport


def post_probe() -> dict:
    response = TestClient(server.app).post(PROBE_URL, json={})
    assert response.status_code == 200, response.text
    assert sorted(response.json()) == PAYLOAD_KEYS
    return response.json()


@pytest.mark.parametrize("error", [httpx.ConnectError("refused"), httpx.ConnectTimeout("silent")])
def test_reason_one_service_not_running_names_the_stage_and_skips_the_engine(monkeypatch, error):
    enable_local_vision()
    factory, transport = record_probe_http(monkeypatch, get_error=error)
    payload = post_probe()
    assert payload["reason"] == REASON_SERVICE_NOT_RUNNING and payload["stage"] == STAGE_SERVICE
    assert payload["status_code"] is None and payload["ok"] is False
    assert transport.post.call_count == 0, "服务没开时不该再发那次生成请求"
    assert factory.call_count == 1
    assert payload["message"] == messages()[REASON_SERVICE_NOT_RUNNING]


def test_reason_two_model_absent_is_decided_from_the_list_alone(monkeypatch):
    enable_local_vision()
    factory, transport = record_probe_http(
        monkeypatch, tags=http_response(200, body=tags_payload("someone-elses-model:9")))
    payload = post_probe()
    assert payload["reason"] == REASON_MODEL_NOT_FOUND and payload["stage"] == STAGE_MODEL
    assert transport.post.call_count == 0, "名字不在清单里就不该去冷载入模型"
    assert factory.call_count == 1


def test_reason_three_wrong_prefix_is_caught_by_the_cheap_step(monkeypatch):
    enable_local_vision(base_url=f"{LOCAL_BASE}/v1")
    factory, transport = record_probe_http(
        monkeypatch, tags=http_response(404, raw_text="404 page not found"))
    payload = post_probe()
    assert payload["reason"] == REASON_ADDRESS_NOT_OLLAMA and payload["stage"] == STAGE_SERVICE
    assert payload["status_code"] == 404
    assert transport.get.call_args.args[0] == "http://127.0.0.1:11434/v1/api/tags"
    assert transport.post.call_count == 0
    assert payload["checked"] == {"base_url": f"{LOCAL_BASE}/v1", "model": LOCAL_MODEL}


def test_unrecognised_tags_shape_falls_through_to_exactly_one_generation(monkeypatch):
    """清单形状认不出来时不臆断成失败，改用那次真请求的证据（404 + JSON error 仍是模型名）。"""
    enable_local_vision()
    factory, transport = record_probe_http(
        monkeypatch, tags=http_response(200, body={"unexpected": 1}),
        chat=http_response(404, body={"error": f"model '{SENTINEL}' not found"}))
    payload = post_probe()
    assert payload["reason"] == REASON_MODEL_NOT_FOUND and payload["stage"] == STAGE_GENERATION
    assert payload["status_code"] == 404 and SENTINEL not in json.dumps(payload, ensure_ascii=False)
    assert len(local_calls(transport)) == 1


def test_reason_four_truncation_comes_from_done_reason_and_never_reads_a_picture(monkeypatch):
    enable_local_vision()
    factory, transport = record_probe_http(
        monkeypatch, chat=ollama_response({"probe": "ok"}, done_reason="length"))
    payload = post_probe()
    assert payload["reason"] == REASON_OUTPUT_TRUNCATED and payload["stage"] == STAGE_GENERATION
    assert payload["ok"] is False and payload["status_code"] == 200
    assert len(local_calls(transport)) == 1
    assert "images" not in json.dumps(sent_body(transport), ensure_ascii=False)


def test_a_healthy_probe_answers_passed_after_two_requests(monkeypatch):
    enable_local_vision()
    factory, transport = record_probe_http(monkeypatch)
    payload = post_probe()
    assert payload["ok"] is True and payload["reason"] == REASON_PASSED
    assert payload["stage"] == STAGE_GENERATION and payload["status_code"] == 200
    assert len(local_calls(transport)) == 1 and cloud_calls(transport) == []
    assert factory.call_count == 1, "两步共用一个客户端"
    assert sent_body(transport)["options"] == {"num_ctx": 4096, "num_predict": 1200}
    for call in (transport.get.call_args, transport.post.call_args):
        assert "headers" not in call.kwargs, "探针两步都不带任何鉴权头"


@pytest.mark.parametrize("error,stage", [(httpx.ReadTimeout("slow"), STAGE_SERVICE),
                                         (httpx.PoolTimeout("busy"), STAGE_GENERATION)])
def test_timeout_after_the_connection_is_reported_as_no_reply(monkeypatch, error, stage):
    """服务没开与没回话是两句话：前者是连接阶段失败，后者已经发出去在等。"""
    enable_local_vision()
    if stage == STAGE_SERVICE:
        record_probe_http(monkeypatch, get_error=error)
    else:
        record_probe_http(monkeypatch, chat_error=error)
    payload = post_probe()
    assert payload["reason"] == REASON_NO_REPLY and payload["stage"] == stage


@pytest.mark.parametrize("configured,client_budget,tags_budget",
                         [(600, PROBE_GENERATION_TIMEOUT_SECONDS, PROBE_SERVICE_TIMEOUT_SECONDS),
                          (45, 45.0, 5.0), (1, 1.0, 1.0)])
def test_probe_budgets_never_outrun_the_browser_default(monkeypatch, configured, client_budget,
                                                         tags_budget):
    enable_local_vision(timeout_seconds=configured)
    factory, transport = record_probe_http(monkeypatch)
    post_probe()
    assert factory.call_args.kwargs["timeout"] == client_budget
    assert transport.get.call_args.kwargs["timeout"] == tags_budget
    assert client_budget + tags_budget < 120


def test_probe_never_writes_and_public_entry_stays_byte_identical(monkeypatch):
    enable_local_vision()
    enable_text()
    before_private = v2_path().read_bytes()
    before_public = TestClient(server.app).get("/api/ai-capabilities").json()["capabilities"]
    record_probe_http(monkeypatch)
    post_probe()
    assert v2_path().read_bytes() == before_private
    after = TestClient(server.app).get("/api/ai-capabilities").json()["capabilities"]
    assert after == before_public
    assert after["vision"]["connection_status"] == "not_tested"


def test_a_failed_probe_writes_nothing_either(monkeypatch):
    enable_local_vision()
    before = v2_path().read_bytes()
    record_probe_http(monkeypatch, get_error=httpx.ConnectError("refused"))
    payload = post_probe()
    assert payload["reason"] == REASON_SERVICE_NOT_RUNNING
    assert v2_path().read_bytes() == before


def test_without_a_saved_v2_nothing_is_sent_and_no_file_appears(monkeypatch):
    factory, transport = record_probe_http(monkeypatch)
    payload = post_probe()
    assert payload["reason"] == REASON_NOT_TESTABLE and payload["stage"] == STAGE_CONFIG
    assert factory.call_count == 0 and transport.post.call_count == 0
    assert not v2_path().exists()
    assert not llm_config.CONFIG_PATH.exists()


@pytest.mark.parametrize("changes", [{"enabled": False}, {"needs_confirmation": True},
                                     {"execution_location": "cloud"}, {"adapter": "openai_chat"}])
def test_the_config_gate_refuses_four_states_without_opening_a_socket(monkeypatch, changes):
    enable_local_vision()
    entry = {**enable_local_vision()["capabilities"]["vision"], **changes}
    monkeypatch.setattr(probe, "read_capability_settings",
                        lambda: {"capabilities": {"vision": entry}})
    factory, transport = record_probe_http(monkeypatch)
    payload = probe.run_local_vision_probe()
    assert payload["reason"] == REASON_NOT_TESTABLE and payload["stage"] == STAGE_CONFIG
    assert factory.call_count == 0 and transport.post.call_count == 0
    assert sorted(payload["checked"]) == ["base_url", "model"]


def test_a_saved_cloud_vision_entry_is_declared_untestable_by_the_endpoint(monkeypatch):
    enable_cloud_vision()
    factory, transport = record_probe_http(monkeypatch)
    payload = post_probe()
    assert payload["reason"] == REASON_NOT_TESTABLE
    assert factory.call_count == 0 and transport.get.call_count == 0


def test_an_external_address_never_leaves_the_machine(monkeypatch):
    """非回环地址在发出任何请求之前就被 local_chat_endpoint 挡掉。

    这种条目走正经保存路径是写不进 v2 的（校验就拒了），所以这里直接换掉探针取配置的那一步，
    专门验探针自己这一层的防线——发什么地址由 vision_request_config 从磁盘取回的那份说了算。
    """
    enable_local_vision()
    monkeypatch.setattr(probe, "vision_request_config", lambda legacy: LocalVisionConfig(
        "http://10.0.0.5:11434", LOCAL_MODEL, 45, 1))
    factory, transport = record_probe_http(monkeypatch)
    payload = probe.run_local_vision_probe()
    assert payload["reason"] == REASON_ADDRESS_INVALID and payload["stage"] == STAGE_CONFIG
    assert factory.call_count == 0 and transport.get.call_count == 0


def test_a_doctored_gate_cannot_make_the_probe_send_to_a_cloud_entry(monkeypatch):
    """磁盘上存的是云端条目时，就算闸门读到一份假的"本机已启用"也不会发出任何请求。

    这条是把上面那条测出来的事实钉住：真正决定发什么的是 vision_request_config 从磁盘取回的
    那份配置，闸门只是提前给一句人话。
    """
    enable_cloud_vision()
    gate_entry = {"enabled": True, "needs_confirmation": False, "execution_location": "local",
                  "adapter": "ollama_chat", "base_url": LOCAL_BASE, "model": LOCAL_MODEL}
    monkeypatch.setattr(probe, "read_capability_settings",
                        lambda: {"capabilities": {"vision": gate_entry}})
    factory, transport = record_probe_http(monkeypatch)
    payload = probe.run_local_vision_probe()
    assert payload["reason"] == REASON_NOT_TESTABLE and payload["stage"] == STAGE_CONFIG
    assert factory.call_count == 0 and transport.get.call_count == 0 and transport.post.call_count == 0


def test_a_legacy_v1_file_on_disk_does_not_leak_into_the_probe(monkeypatch):
    enable_local_vision()
    llm_config.save_llm_settings(base_url="https://legacy.invalid/v1", model="legacy-model",
                                 timeout_seconds=30, max_images=4, api_key="legacy-v1-key-445566")
    factory, transport = record_probe_http(monkeypatch)
    payload = post_probe()
    assert payload["checked"] == {"base_url": LOCAL_BASE, "model": LOCAL_MODEL}
    assert_no_secret(json.dumps(payload, ensure_ascii=False))
    with pytest.raises(AssertionError):
        probe._no_legacy_loader()


def test_output_carries_no_key_fragment_and_no_upstream_text(monkeypatch):
    enable_local_vision()
    enable_text()
    factory, transport = record_probe_http(
        monkeypatch, tags=http_response(404, raw_text=f"404 page not found {TEXT_KEY}"))
    response = TestClient(server.app).post(PROBE_URL, json={})
    assert_no_secret(response.text)
    assert TEXT_KEY not in response.text
    assert transport.post.call_args is None, "第一步就 404 时不该再发那次生成请求"
    for call in (transport.get.call_args, transport.post.call_args):
        if call is not None:
            assert "headers" not in call.kwargs


def test_the_probe_never_touches_the_image_helpers(monkeypatch):
    from app.core import local_vision_client
    enable_local_vision()
    factory, transport = record_probe_http(monkeypatch)
    monkeypatch.setattr(local_vision_client, "encode_local_image",
                        Mock(side_effect=AssertionError("探针不得读素材图片")))
    monkeypatch.setattr(local_vision_client, "select_first_valid_image",
                        Mock(side_effect=AssertionError("探针不得挑素材图片")))
    assert post_probe()["ok"] is True


@pytest.mark.parametrize("method,path,status", [
    ("post", PROBE_URL, 200), ("get", PROBE_URL, 404),
    ("post", TEXT_PROBE_URL, 200), ("get", TEXT_PROBE_URL, 404),
    ("post", "/api/ai-capabilities/image/test", 405)])
def test_route_surface_is_exactly_two_post_endpoints(monkeypatch, method, path, status):
    """状态码是 2026-10-14 实测：这些没匹配上的形状落在根路径的静态挂载上——GET 找不着文件回 404，
    而挂载不接受 POST 回 405，都不是我以为的"路由层给 404"。

    本条真正要钉的是两件事：探针只有图片理解与文字推理这两个 POST 入口，第三个能力的 test 口不存在；
    任何别的形状都不会让探针开出一个 socket。
    """
    enable_local_vision()
    factory, transport = record_probe_http(monkeypatch)
    client = TestClient(server.app)
    response = client.request(method, path, json={})
    assert response.status_code == status
    if (method, path) not in PROBE_ROUTES:
        assert factory.call_count == 0 and transport.post.call_count == 0


@pytest.mark.parametrize("body", [{"foo": 1}, {"cloud_authorized": True},
                                  {"cloud_authorized": False, "base_url": "http://10.0.0.9:11434"}])
def test_the_endpoint_takes_no_parameters_and_no_address_from_the_browser(monkeypatch, body):
    enable_local_vision()
    factory, transport = record_probe_http(monkeypatch)
    response = TestClient(server.app).post(PROBE_URL, json=body)
    assert response.status_code == 400
    assert response.json()["detail"] == "测试连接不接受任何参数"
    assert_no_secret(response.text)
    assert factory.call_count == 0


def test_cross_origin_probe_is_rejected_before_any_socket(monkeypatch):
    enable_local_vision()
    factory, transport = record_probe_http(monkeypatch)
    response = TestClient(server.app).post(PROBE_URL, json={},
                                          headers={"Origin": "https://other.invalid"})
    assert response.status_code == 403
    assert factory.call_count == 0 and transport.post.call_count == 0


@pytest.mark.parametrize("capability", ["image", "video", "unknown"])
def test_the_one_segment_capability_routes_still_answer_not_found(capability):
    assert TestClient(server.app).post(f"/api/ai-capabilities/{capability}", json={}).status_code == 404


def test_three_machine_reachable_reasons_read_as_three_different_sentences(monkeypatch):
    """合格标准里「界面报的话互不相同」的离线版：三种能真机造出来的原因，句子两两不等。"""
    cases = [dict(get_error=httpx.ConnectError("refused")),
             dict(tags=http_response(200, body=tags_payload("someone-elses-model:9"))),
             dict(tags=http_response(404, raw_text="404 page not found"))]
    seen = []
    for kwargs in cases:
        enable_local_vision()
        record_probe_http(monkeypatch, **kwargs)
        payload = post_probe()
        seen.append(payload["message"])
    assert len(set(seen)) == 3, "三种失败原因在界面上必须是一句不同的话"


def test_a_saved_local_entry_survives_a_probe_without_losing_its_fields(monkeypatch):
    """探测后再读公开接口：图片理解那一格的每个字段都得还是原来那份。"""
    written = enable_local_vision()
    record_probe_http(monkeypatch)
    post_probe()
    public = TestClient(server.app).get("/api/ai-capabilities").json()["capabilities"]["vision"]
    stored = written["capabilities"]["vision"]
    assert public["execution_location"] == stored["execution_location"] == "local"
    assert public["adapter"] == stored["adapter"] == "ollama_chat"
    assert public["max_images"] == stored["max_images"] == 1
    assert public["has_api_key"] is False


# --- 10. 前端文本不变量：本批一个数字都没碰 ---

def test_frontend_keeps_the_pinned_timeout_and_stopwatch_invariants():
    app_js = (Path(server.STATIC_DIR) / "app.js").read_text(encoding="utf-8")
    # 不再抄"长档一共出现几次"这个字面数：那个数归 test_local_vision.py 的元组推导管。
    # 这里只钉探针这一条路自己：它的调用里不许出现长档。
    probe_call = re.search(r"api\(`/api/ai-capabilities/\$\{aiSettingsEditor\.capability\}/test`"
                           r"[\s\S]{0,120}?\)", app_js)
    assert probe_call, "探针的前端调用点不见了"
    assert "MODEL_HEAVY_TIMEOUT_MS" not in probe_call.group(0), "探针必须走 api() 的默认档，不许借用长档"
    assert app_js.count("showElapsedSince(loadingStartedAt,") == 2, "探测不抢秒表"
    assert re.search(r"async function api\(path, options = \{\}, timeoutMs = 120000\)", app_js)
    # 两个能力共用一个调用点：路径由当前 tab 拼出来，所以这里钉的是形状而不是某一条写死的路。
    assert 'api(`/api/ai-capabilities/${aiSettingsEditor.capability}/test`' in app_js
    assert 'api("/api/ai-capabilities/vision/test"' not in app_js, "写死图片理解的那条旧调用还在"


def test_probe_controls_live_inside_the_settings_form():
    index_html = (Path(server.STATIC_DIR) / "index.html").read_text(encoding="utf-8")
    # 「尚未测试连接」那一行的原文一个字没动：它有自己的钉测试（test_ai_settings_entry.py:211）。
    assert 'id="aiSettingsStatus" role="status" aria-live="polite">尚未测试连接<' in index_html
    form = index_html.split('<form class="add-product-form" id="aiSettingsForm"', 1)[1].split("</form>", 1)[0]
    assert 'id="aiConnectionTest"' in form and 'id="aiProbeResult"' in form, "[hidden] 规则只在 form 内生效"
    assert 'data-action="test-local-connection"' in form


# --- 11. node-vm 真跑 app.js：按钮的显示条件、四句话的渲染、以及不许碰的那一行 ---

# 服务端话术由这张表代入 JS：界面上出现的每一句都和后端那张表逐字比对，测试里不另抄一份中文。
_PROBE_PAYLOADS = [
    {"ok": False, "reason": REASON_SERVICE_NOT_RUNNING, "stage": STAGE_SERVICE,
     "message": messages()[REASON_SERVICE_NOT_RUNNING]},
    {"ok": False, "reason": REASON_MODEL_NOT_FOUND, "stage": STAGE_MODEL,
     "message": messages()[REASON_MODEL_NOT_FOUND]},
    {"ok": False, "reason": REASON_ADDRESS_NOT_OLLAMA, "stage": STAGE_SERVICE,
     "message": messages()[REASON_ADDRESS_NOT_OLLAMA]},
    {"ok": False, "reason": REASON_OUTPUT_TRUNCATED, "stage": STAGE_GENERATION,
     "message": messages()[REASON_OUTPUT_TRUNCATED]},
    {"ok": True, "reason": REASON_PASSED, "stage": STAGE_GENERATION,
     "message": messages()[REASON_PASSED]},
    {"ok": True, "reason": REASON_PASSED, "stage": STAGE_GENERATION,
     "message": messages()[REASON_PASSED]},
    {"ok": False, "reason": "a_code_written_after_this_test", "stage": STAGE_GENERATION},
    # 文字 tab 那一次探测的应答，本脚本只用它验路径；话术由 test_local_text_probe.py 逐条钉。
    {"ok": True, "reason": REASON_PASSED, "stage": STAGE_GENERATION,
     "message": messages()[REASON_PASSED]},
]

_PROBE_SCRIPT = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const nodes = {};
const node = id => nodes[id] ||= {value:'',checked:false,disabled:false,required:false,hidden:true,
  textContent:'',classList:{add(){},remove(){}},focus(){},reset(){},reportValidity(){return true;}};
const entries = {text:{enabled:false,execution_location:'cloud',base_url:'',model:'',timeout_seconds:90,
                       has_api_key:false,needs_confirmation:false},
                 vision:{enabled:true,execution_location:'local',adapter:'ollama_chat',
                         base_url:'http://127.0.0.1:11434',model:'qwen3-vl:2b',timeout_seconds:45,
                         max_images:1,has_api_key:false,needs_confirmation:false}};
const requests = [], seen = [];
const tabs = ['text','vision'].map(capability=>({dataset:{capability},setAttribute(n,v){this[n]=v;}}));
const context = {document:{addEventListener(){},getElementById:node,querySelectorAll(){return tabs;}},
                 window:{confirm(){return true;}},setTimeout,clearTimeout,AbortController};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'), context);
Object.assign(context, {requests, seen, entries, nodes});
// 响应列表整个代进 vm 内部，让桩每次按顺序取下一条；nodes 必须一起塞进上下文，
// 否则桩里读不到那些假节点，api 会直接抛 ReferenceError——第一版就是这么把四句渲染跑成兜底句的。
vm.runInContext(`let probeIndex = 0;
const probePayloads = __PAYLOADS__;
api = async (path, options) => {
  requests.push({path, body: options ? options.body : null});
  if (path === '/api/ai-capabilities') return {capabilities: entries};
  seen.push([nodes.aiProbeResult.textContent, nodes.aiSettingsSave.disabled,
             nodes.aiConnectionTest.disabled, nodes.aiSettingsStatus.textContent]);
  return probePayloads[probeIndex++];
}`, context);
const serverPayloads = __PAYLOADS__;
(async () => {
  await context.showAISettings();

  // 文字 tab 的执行位置是 local 时也必须出现按钮：本机文字已有自己的探针（/api/ai-capabilities/text/test）。
  // 真正该收起的只有云端——云端这一条路根本不经过本机 Ollama，探它等于探一条不存在的路。
  context.renderAISettings('text');
  node('aiLocation').value = 'local';
  context.syncAISettingsFields();
  assert.strictEqual(node('aiConnectionTest').hidden, false, '文字 tab 也该出现探测按钮');

  context.renderAISettings('vision');
  node('aiLocation').value = 'local';
  context.syncAISettingsFields();
  assert.strictEqual(node('aiConnectionTest').hidden, false);

  // 云端图片理解同样收起，且结果行一起收起，不留一句过期结论在屏幕上。
  node('aiLocation').value = 'cloud';
  context.syncAISettingsFields();
  assert.strictEqual(node('aiConnectionTest').hidden, true);
  assert.strictEqual(node('aiProbeResult').hidden, true);
  node('aiLocation').value = 'local';
  context.syncAISettingsFields();

  const statusBefore = node('aiSettingsStatus').textContent;
  assert(statusBefore.includes('尚未测试连接'), statusBefore);
  for (let i = 0; i < 4; i++) {
    await context.testLocalConnection();
    assert.strictEqual(node('aiProbeResult').textContent, __PREFIX__ + serverPayloads[i].message);
    assert.strictEqual(node('aiSettingsStatus').textContent, statusBefore,
      '探测不许改写「尚未测试连接」那一行');
    assert.strictEqual(node('aiSettingsSave').disabled, false);
    assert.strictEqual(node('aiConnectionTest').disabled, false);
  }
  assert.strictEqual(seen.length, 4);
  for (const observed of seen) {
    assert.strictEqual(observed[0], __PENDING__, '发请求前必须先把进行中那句写上');
    assert.strictEqual(observed[1], true, '探测期间禁用保存');
    assert.strictEqual(observed[2], true, '探测期间按钮自身也禁用');
  }

  await context.testLocalConnection();
  assert.strictEqual(node('aiProbeResult').textContent, serverPayloads[4].message, '通过时不加失败前缀');

  // 输入框有未保存的改动时，必须说清楚这次测的是磁盘上那份。
  node('aiModel').value = 'draft-model-not-saved';
  await context.testLocalConnection();
  assert(node('aiProbeResult').textContent.endsWith(__SUFFIX__), node('aiProbeResult').textContent);
  node('aiModel').value = entries.vision.model;

  // 后端没给 message 时也不能渲染出 undefined。
  await context.testLocalConnection();
  assert.strictEqual(node('aiProbeResult').textContent, __PREFIX__ + __FALLBACK__);
  assert.strictEqual(seen.length, 7);

  // 切到文字 tab，同一套代码拼出的路径必须跟着变成 text/test：按钮只有一套，探的是当前那一格。
  // 这里只钉路径怎么拼；文字探针本身的话术与判定归 tests/test_local_text_probe.py。
  context.renderAISettings('text');
  node('aiLocation').value = 'local';
  context.syncAISettingsFields();
  assert.strictEqual(node('aiConnectionTest').hidden, false, '文字 tab 本机也要有按钮');
  await context.testLocalConnection();
  assert.strictEqual(requests[requests.length - 1].path, '/api/ai-capabilities/text/test');
  assert.strictEqual(requests.filter(r => r.path === '/api/ai-capabilities/vision/test').length, 7,
    '文字 tab 之后不许再发图片理解那条路');

  const count = requests.length;
  vm.runInContext('aiSettingsEditor.saving = true', context);
  await context.testLocalConnection();
  assert.strictEqual(requests.length, count, '进行中之时不得再发第二次探测');
  vm.runInContext('aiSettingsEditor.saving = false', context);

  vm.runInContext(`api = async () => { throw new Error("请求超时，请检查网站是否在运行后重试"); }`, context);
  await context.testLocalConnection();
  assert(node('aiProbeResult').textContent.startsWith('本次测试未能完成：请求超时'));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''


def test_the_frontend_renders_each_reason_as_its_own_sentence():
    node = shutil.which("node")
    assert node
    tokens = {
        "__PAYLOADS__": json.dumps(_PROBE_PAYLOADS, ensure_ascii=False),
        "__PENDING__": json.dumps("正在测试本机连接。第一次调用要把模型读进内存，可能明显偏慢，"
                                  "请不要关闭这个页面。", ensure_ascii=False),
        "__PREFIX__": json.dumps("本次测试没通过。", ensure_ascii=False),
        "__SUFFIX__": json.dumps("（本次测的是已保存的设置，输入框里的改动还没保存）", ensure_ascii=False),
        # 这句只在 JS 里（后端没给 message、或异常文案不合白名单时兜底），跟 app.js 逐字对齐。
        "__FALLBACK__": json.dumps("本次测试未能完成，后端没有给出可判定的原因；未确认任何连接结果。",
                                   ensure_ascii=False),
    }
    script = _PROBE_SCRIPT
    for token, value in tokens.items():
        script = script.replace(token, value)
    result = subprocess.run([node, "-e", script, str(Path(server.STATIC_DIR) / "app.js")],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr
