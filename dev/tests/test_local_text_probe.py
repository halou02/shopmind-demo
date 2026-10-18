"""「文字推理 · 本机」连接自检的离线测试：全假客户端、全假响应，零真实请求。

桩位沿用 tests/test_local_vision.py 的 record_http：app.core.local_text_probe 里的 httpx
与它是同一个模块对象，挂一次就同时覆盖两层。探针第一步是 GET 模型清单而 record_http 只桩 POST，
所以本文件再包一层 record_text_http 显式喂 transport.get——不去改那份被十几条测试依赖的公共夹具。
"""
from __future__ import annotations

import hashlib
import json
import re
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core import capability_config
from app.core import local_text_probe as text_probe
from app.core import local_vision_probe as vision_probe
from app.core.llm_config import LocalTextConfig
from app.core.local_text_probe import (
    REASON_ADDRESS_INVALID, REASON_ADDRESS_MISSING_V1, REASON_BAD_ENVELOPE,
    REASON_MODEL_NOT_FOUND, REASON_NO_REPLY, REASON_NOT_TESTABLE, REASON_PASSED,
    REASON_SERVICE_NOT_RUNNING, REASON_UNKNOWN, REASON_UPSTREAM_ERROR,
    STAGE_CONFIG, STAGE_GENERATION, STAGE_MODEL, STAGE_SERVICE, TEXT_PROBE_MAX_TOKENS,
    TEXT_PROBE_REASONS, TextProbeOutcome, build_text_probe_body, compat_suffix_missing,
    classify_text_completion, classify_text_tags_response, text_probe_endpoints)
from tests.test_local_vision import LOCAL_BASE, TEXT_BASE, TEXT_KEY, assert_no_secret, enable_text, record_http
from tests.test_local_vision_probe import (PAYLOAD_KEYS, PROBE_URL, SENTINEL, http_response,
                                            tags_payload)
from app.web import server

TEXT_PROBE_URL = "/api/ai-capabilities/text/test"
LOCAL_TEXT_BASE = f"{LOCAL_BASE}/v1"
# 本机实际装着的那个文字模型（ollama list 实测）。判定只看这个名字在不在清单里，字符串本身不触发任何行为。
TEXT_MODEL = "qwen2.5:1.5b-instruct"


def messages() -> dict[str, str]:
    return text_probe._TEXT_MESSAGES


def enable_local_text(**changes) -> dict:
    payload = {"enabled": True, "execution_location": "local", "base_url": LOCAL_TEXT_BASE,
               "model": TEXT_MODEL, "timeout_seconds": 45,
               "credential_action": "clear", "api_key": None}
    payload.update(changes)
    return enable_text(**payload)


def completion(content: str = '{"probe":"ok"}', *, finish_reason: str = "stop") -> Mock:
    """OpenAI 兼容口的成功信封。与图片理解那套原生信封（done / done_reason）形状完全不同。"""
    return http_response(200, body={"choices": [{"index": 0, "finish_reason": finish_reason,
                                                "message": {"role": "assistant",
                                                            "content": content}}],
                                    "usage": {"total_tokens": 7}})


def record_text_http(monkeypatch, *, tags: Mock | None = None, chat: Mock | None = None,
                     get_error: BaseException | None = None,
                     chat_error: BaseException | None = None):
    factory, transport = record_http(monkeypatch, response=chat if chat is not None else completion())
    if get_error is not None:
        transport.get.side_effect = get_error
    else:
        transport.get.return_value = tags if tags is not None else http_response(
            200, body=tags_payload(TEXT_MODEL))
    if chat_error is not None:
        transport.post.side_effect = chat_error
    return factory, transport


def post_text_probe(body: dict | None = None) -> dict:
    response = TestClient(server.app).post(TEXT_PROBE_URL, json={} if body is None else body)
    assert response.status_code == 200, response.text
    assert sorted(response.json()) == PAYLOAD_KEYS
    return response.json()


# --- 1. 话术表：十个原因各一句，六句与图片理解同源，四句是文字专属 ---

def test_every_reason_has_exactly_one_message_and_no_message_is_orphaned():
    assert set(messages()) == set(TEXT_PROBE_REASONS)
    assert len(TEXT_PROBE_REASONS) == 10


@pytest.mark.parametrize("reason", text_probe._REUSED)
def test_the_six_shared_sentences_are_literally_the_vision_ones(reason):
    """断言 `is` 而不是 `==`：同源就必须是同一个字符串对象，抄一份就会各自漂移。"""
    assert messages()[reason] is vision_probe._MESSAGES[reason]


def test_the_four_text_only_reasons_do_not_reuse_the_vision_sentence():
    for reason in (REASON_PASSED, REASON_NOT_TESTABLE, REASON_BAD_ENVELOPE):
        assert messages()[reason] != vision_probe._MESSAGES[reason], f"{reason} 照抄了视觉那句"
    # 第四条本来就是文字专属的原因码：视觉那张表里根本没有这个键，谈不上照抄。
    assert REASON_ADDRESS_MISSING_V1 in messages()
    assert REASON_ADDRESS_MISSING_V1 not in vision_probe._MESSAGES


def test_no_text_sentence_tells_the_user_to_drop_the_v1_suffix():
    """视觉那条的处方是「不要带 /v1」，对文字正好相反。合并两张表或照抄就会把人带进死路。"""
    dumped = json.dumps(messages(), ensure_ascii=False)
    assert vision_probe.REASON_ADDRESS_NOT_OLLAMA not in messages()
    assert "不要带 /v1" not in dumped
    assert "/v1" in messages()[REASON_ADDRESS_MISSING_V1]
    assert "本程序不会替你补" in messages()[REASON_ADDRESS_MISSING_V1]
    assert "/v1" in messages()[REASON_BAD_ENVELOPE]


def test_the_passed_sentence_discloses_what_a_pass_does_not_prove():
    """探针只让模型写 64 个 token，所以「通过」绝不等于文案质量合格，也不等于真实生成不被截断。"""
    text = messages()[REASON_PASSED]
    assert "证明不了真实生成不会被输出上限截断" in text
    assert "它证明不了文案的质量" in text


def test_reason_codes_do_not_collide_with_the_vision_table_under_a_different_meaning():
    """两个能力共用一套前端渲染，reason 只能有一个含义：文字表允许缺项，不许同名异义。"""
    assert set(TEXT_PROBE_REASONS) - set(vision_probe.PROBE_REASONS) == {REASON_ADDRESS_MISSING_V1}


@pytest.mark.parametrize("reason", TEXT_PROBE_REASONS)
def test_message_is_one_plain_line_without_duration_or_banned_token(reason):
    text = messages()[reason]
    assert text.strip() == text and "\n" not in text
    # 不写死秒数：这台无独显机器上的冷载入耗时对队友的机器不成立
    assert not re.search(r"\d+\s*秒", text), text
    lowered = text.lower()
    for token in ("connected", "success", "verified", "reachable", "healthy"):
        assert token not in lowered, f"{reason} 的文案里有禁词 {token}"
    for token in (SENTINEL, ".private.json", "api_key"):
        assert token not in text


# --- 2. 地址派生：探针拼的路径必须和真实调用逐字同构 ---

def test_chat_url_matches_the_real_openai_compatible_call_exactly():
    """真实调用是 f"{validate_base_url(base_url)}/chat/completions"（app/core/llm_client.py:57）。"""
    chat, tags = text_probe_endpoints(LOCAL_TEXT_BASE)
    assert chat == "http://127.0.0.1:11434/v1/chat/completions"
    # 清单走 origin 根：它和末尾挂没挂 /v1 无关，所以「服务在不在、名字对不对」与「后缀全不全」能分开判。
    assert tags == "http://127.0.0.1:11434/api/tags"


def test_a_missing_v1_survives_into_the_chat_url_instead_of_being_padded():
    chat, tags = text_probe_endpoints(LOCAL_BASE)
    assert chat == "http://127.0.0.1:11434/chat/completions"
    assert tags == "http://127.0.0.1:11434/api/tags"


def test_localhost_is_not_rewritten_because_the_real_call_does_not_rewrite_it():
    """图片理解会把字面 localhost 换成 127.0.0.1（local_chat_endpoint），文字的真实调用不会。
    探针要复现现实，所以这里必须保留原样——两条路的差别是代码事实，不是笔误。"""
    chat, tags = text_probe_endpoints("http://localhost:11434/v1")
    assert chat == "http://localhost:11434/v1/chat/completions"
    assert tags == "http://localhost:11434/api/tags"


@pytest.mark.parametrize("base_url,missing", [
    (LOCAL_BASE, True), (LOCAL_BASE + "/", True), ("http://127.0.0.1:11434/V1", False),
    (LOCAL_TEXT_BASE, False), (LOCAL_TEXT_BASE + "/", False),
    # 把整条接口地址粘进来的那种：路径结尾不是 /v1，所以这道闸门一样回 True。
    # 归到「少写 /v1」这个名字是不精确的（它其实是尾巴多了一段），但这句话给的处方仍然对：
    # 照「写成 http://127.0.0.1:11434/v1」去改，两种形状都会好。见下一条用例。
    ("http://127.0.0.1:11434/v1/chat/completions", True)])
def test_compat_suffix_gate_reads_only_the_trailing_path(base_url, missing):
    assert compat_suffix_missing(base_url) is missing


def test_an_over_pasted_interface_url_gets_the_same_fix_as_an_under_pasted_one():
    """粘进整条 /v1/chat/completions 时，真实调用会拼出双份 /chat/completions，探针照样一步就定性。"""
    base = "http://127.0.0.1:11434/v1/chat/completions"
    chat, _ = text_probe_endpoints(base)
    assert chat == "http://127.0.0.1:11434/v1/chat/completions/chat/completions"
    assert compat_suffix_missing(base) is True


# --- 3. 探针请求体：只让模型写几个字，且只用兼容口真正认的那个参数名 ---

def test_probe_body_caps_output_with_the_parameter_the_compatible_endpoint_honors():
    body = build_text_probe_body(LocalTextConfig(LOCAL_TEXT_BASE, TEXT_MODEL, 45))
    assert sorted(body) == ["max_tokens", "messages", "model", "stream", "temperature"]
    assert body["model"] == TEXT_MODEL
    assert body["max_tokens"] == TEXT_PROBE_MAX_TOKENS == 64
    assert body["stream"] is False and body["temperature"] == 0
    assert body["messages"] == [{"role": "user", "content": '只回复一个 JSON 对象：{"probe":"ok"}'}]
    serialized = json.dumps(body)
    # num_predict 会被兼容口静默忽略（本机实测：发 20 仍回 529 个 token），照抄视觉那套等于没设。
    assert "num_predict" not in serialized
    # 不发 response_format：某个模型不支持 JSON 模式，不该被误报成连接失败。
    assert "response_format" not in serialized


# --- 4. 两步判定 ---

@pytest.mark.parametrize("status,body,reason", [
    (200, tags_payload(TEXT_MODEL), REASON_PASSED),
    (200, tags_payload("someone-elses-model:9"), REASON_MODEL_NOT_FOUND),
    (200, None, REASON_BAD_ENVELOPE),
    (404, None, REASON_BAD_ENVELOPE),
    (404, {"error": SENTINEL}, REASON_BAD_ENVELOPE),
    (500, None, REASON_UPSTREAM_ERROR),
    (503, None, REASON_UPSTREAM_ERROR),
    (301, None, REASON_BAD_ENVELOPE),
    (307, None, REASON_BAD_ENVELOPE)])
def test_tags_step_classification(status, body, reason):
    assert classify_text_tags_response(status, body, TEXT_MODEL) == reason


def test_a_200_tags_body_of_unrecognized_shape_probes_on_instead_of_guessing():
    """清单形状认不出来时不下结论，让下一步的真生成去证——沿用 classify_tags_body 的「绝不臆断成失败」。

    代价要写明白：这种极端形状下，「通过」那句里「给出了模型清单」这一条其实没成立。
    这条用例就是把取舍钉在明面上，而不是让它藏在实现里。
    """
    assert classify_text_tags_response(200, {"detail": "x"}, TEXT_MODEL) == REASON_PASSED


@pytest.mark.parametrize("status,body,reason", [
    (200, {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}, REASON_PASSED),
    # 撞上限：探针自己只给 64 个 token，截断在这里判不出来，也不该据此报失败（口径由「通过」那句披露）。
    (200, {"choices": [{"message": {"content": "写了一半"}, "finish_reason": "length"}]}, REASON_PASSED),
    (200, {"choices": [{"message": {"content": "   "}}]}, REASON_BAD_ENVELOPE),
    (200, {"choices": []}, REASON_BAD_ENVELOPE),
    (200, {"data": "ok"}, REASON_BAD_ENVELOPE),
    (200, None, REASON_BAD_ENVELOPE),
    # 同一个 404 的两种真实形状：模型名不存在是带 error 的 JSON，路径没挂对是纯文本。
    (404, {"error": f"model '{SENTINEL}' not found"}, REASON_MODEL_NOT_FOUND),
    (404, {"error": {"message": SENTINEL}}, REASON_MODEL_NOT_FOUND),
    (404, None, REASON_BAD_ENVELOPE),
    (404, {"detail": "Not Found"}, REASON_BAD_ENVELOPE),
    (400, {"error": "bad"}, REASON_UPSTREAM_ERROR),
    (401, {"error": SENTINEL}, REASON_UPSTREAM_ERROR),
    (500, None, REASON_UPSTREAM_ERROR),
    (302, None, REASON_BAD_ENVELOPE)])
def test_completion_step_classification(status, body, reason):
    assert classify_text_completion(status, body) == reason


def test_a_length_finish_reason_is_not_mistaken_for_a_truncation_verdict():
    """探针不看 finish_reason：真实生成的上限是 4000，探针那 64 个 token 撞不撞顶说明不了任何事。"""
    assert classify_text_completion(200, completion("半句话", finish_reason="length").json()) \
        == REASON_PASSED


def test_classify_text_completion_never_blames_the_missing_v1_suffix():
    """走到这一步说明地址确实以 /v1 结尾（静态闸门已在零请求阶段拦下），再怪后缀就是假话。"""
    shapes = [None, {}, {"error": "x"}, {"choices": [{"message": {"content": "ok"}}]}]
    verdicts = {classify_text_completion(status, body) for status in (200, 400, 404, 500, 301)
                for body in shapes}
    assert REASON_ADDRESS_MISSING_V1 not in verdicts
    assert REASON_ADDRESS_MISSING_V1 in set(TEXT_PROBE_REASONS), "静态闸门那条结论得有自己的一句话"


# --- 5. 结论载荷的形状：与图片理解完全一致，前端一套渲染能测两个能力 ---

def test_payload_shape_is_the_same_closed_set_as_the_vision_probe():
    payload = TextProbeOutcome(False, REASON_MODEL_NOT_FOUND, STAGE_MODEL, 404).payload(
        {"base_url": LOCAL_TEXT_BASE, "model": TEXT_MODEL})
    assert sorted(payload) == PAYLOAD_KEYS
    assert payload["message"] == messages()[REASON_MODEL_NOT_FOUND]
    assert SENTINEL not in json.dumps(payload, ensure_ascii=False)


def test_unknown_reason_code_falls_back_instead_of_raising():
    outcome = TextProbeOutcome(False, "a_code_nobody_wrote_yet", STAGE_CONFIG)
    assert outcome.message() == messages()[REASON_UNKNOWN]


def test_text_probe_outcome_is_a_probe_outcome_with_its_own_table():
    """同一个父类：按形状写的中间件、渲染、白名单那些守卫不用为第二套探针改；话术表各一份。"""
    assert issubclass(TextProbeOutcome, vision_probe.ProbeOutcome)
    assert TextProbeOutcome.MESSAGES is not vision_probe.ProbeOutcome.MESSAGES
    assert vision_probe.ProbeOutcome(True, REASON_PASSED, STAGE_GENERATION).message() \
        != TextProbeOutcome(True, REASON_PASSED, STAGE_GENERATION).message()


# --- 6. 执行体：两段短路、预算切分、以及每一步省下多少次冷载入 ---

def test_healthy_probe_answers_passed_after_two_requests(monkeypatch):
    enable_local_text()
    factory, transport = record_text_http(monkeypatch)
    payload = post_text_probe()
    assert (payload["ok"], payload["reason"], payload["stage"]) == (True, REASON_PASSED, STAGE_GENERATION)
    assert payload["status_code"] == 200
    assert payload["checked"] == {"base_url": LOCAL_TEXT_BASE, "model": TEXT_MODEL}
    assert payload["message"] == messages()[REASON_PASSED]
    assert factory.call_count == 1, "两步共用一个客户端"
    assert transport.get.call_count == 1 and transport.post.call_count == 1
    assert transport.post.call_args.args[0] == "http://127.0.0.1:11434/v1/chat/completions"
    assert transport.post.call_args.kwargs["json"]["max_tokens"] == TEXT_PROBE_MAX_TOKENS
    # 探针不带任何凭证：这一格结构上没有密钥，头里也不该出现 Authorization。
    for call in (transport.get.call_args, transport.post.call_args):
        assert "headers" not in call.kwargs


def test_probe_budgets_are_split_and_a_one_second_setting_is_honoured(monkeypatch):
    """客户端总预算取 min(配置, 100)，清单那一步单独压到 min(配置, 5)。

    用户把超时设成 1 秒时这里也按 1 秒走：真实链路会一样失败，探针的职责是复现现实，不是替配置美化成绩。
    """
    enable_local_text(timeout_seconds=1)
    factory, transport = record_text_http(monkeypatch)
    assert post_text_probe()["reason"] == REASON_PASSED
    assert factory.call_args.kwargs["timeout"] == 1.0
    assert transport.get.call_args.kwargs["timeout"] == 1.0
    assert factory.call_args.kwargs["trust_env"] is False, "本机探针不许走系统代理"
    assert factory.call_args.kwargs["follow_redirects"] is False


def test_a_45_second_setting_is_capped_before_the_browser_gives_up(monkeypatch):
    enable_local_text()
    factory, transport = record_text_http(monkeypatch)
    post_text_probe()
    assert factory.call_args.kwargs["timeout"] == 45.0
    assert transport.get.call_args.kwargs["timeout"] == 5.0
    assert 5.0 + 45.0 < 120, "两层预算必须留在前端 api() 的 120000 毫秒默认档之下"


@pytest.mark.parametrize("error", [httpx.ConnectError("refused"), httpx.ConnectTimeout("silent")])
def test_reason_one_service_not_running_skips_the_generation_step(monkeypatch, error):
    enable_local_text()
    factory, transport = record_text_http(monkeypatch, get_error=error)
    payload = post_text_probe()
    assert payload["reason"] == REASON_SERVICE_NOT_RUNNING and payload["stage"] == STAGE_SERVICE
    assert payload["status_code"] is None and payload["ok"] is False
    assert transport.post.call_count == 0, "没人应答时不该再去冷载入模型"
    assert payload["message"] == messages()[REASON_SERVICE_NOT_RUNNING]


def test_reason_two_model_absent_is_decided_from_the_list_alone(monkeypatch):
    enable_local_text()
    factory, transport = record_text_http(
        monkeypatch, tags=http_response(200, body=tags_payload("someone-elses-model:9")))
    payload = post_text_probe()
    assert payload["reason"] == REASON_MODEL_NOT_FOUND and payload["stage"] == STAGE_MODEL
    assert transport.post.call_count == 0, "名字不在清单里就不该去冷载入模型"


def test_reason_three_wrong_port_reads_as_an_envelope_that_is_not_ollama(monkeypatch):
    enable_local_text(base_url="http://127.0.0.1:8080/v1")
    factory, transport = record_text_http(
        monkeypatch, tags=http_response(404, raw_text="404 page not found"))
    payload = post_text_probe()
    assert payload["reason"] == REASON_BAD_ENVELOPE and payload["stage"] == STAGE_SERVICE
    assert payload["status_code"] == 404
    assert transport.get.call_args.args[0] == "http://127.0.0.1:8080/api/tags"
    assert transport.post.call_count == 0


def test_the_v1_gate_reaches_its_verdict_without_a_generation_request(monkeypatch):
    """少写 /v1 是纯字符串就能判的：清单 200、名字也在，那就到此为止，不去撞那个必然 404。"""
    enable_local_text(base_url=LOCAL_BASE)
    factory, transport = record_text_http(monkeypatch)
    payload = post_text_probe()
    assert payload["reason"] == REASON_ADDRESS_MISSING_V1 and payload["stage"] == STAGE_MODEL
    assert payload["ok"] is False and payload["status_code"] is None
    assert transport.get.call_count == 1 and transport.post.call_count == 0
    assert payload["message"] == messages()[REASON_ADDRESS_MISSING_V1]


def test_generation_step_timeout_is_attributed_to_the_generation_stage(monkeypatch):
    enable_local_text()
    record_text_http(monkeypatch, chat_error=httpx.ReadTimeout("slow"))
    payload = post_text_probe()
    assert payload["reason"] == REASON_NO_REPLY and payload["stage"] == STAGE_GENERATION


def test_an_upstream_error_status_carries_its_code_but_none_of_its_text(monkeypatch):
    enable_local_text()
    record_text_http(monkeypatch, chat=http_response(500, raw_text=SENTINEL))
    payload = post_text_probe()
    assert payload["reason"] == REASON_UPSTREAM_ERROR and payload["status_code"] == 500
    assert SENTINEL not in json.dumps(payload, ensure_ascii=False)
    assert payload["message"] == messages()[REASON_UPSTREAM_ERROR]


def test_a_non_loopback_address_never_opens_a_client(monkeypatch):
    """配置校验已经拦过一遍，这里是探针自己的第二道闸门：即使脏数据落到磁盘上也不向外部发。"""
    factory, transport = record_text_http(monkeypatch)
    outcome = text_probe.probe_local_text(LocalTextConfig(TEXT_BASE, TEXT_MODEL, 45))
    assert outcome.reason == REASON_ADDRESS_INVALID and outcome.stage == STAGE_CONFIG
    assert factory.call_count == 0 and transport.get.call_count == 0


@pytest.mark.parametrize("bad", ["", "not a url", "http://127.0.0.1:99999/v1",
                                 "ftp://127.0.0.1:11434/v1", "http://127.0.0.1:11434/?x=1"])
def test_a_broken_address_string_converges_instead_of_raising(bad):
    outcome = text_probe.probe_local_text(LocalTextConfig(bad, TEXT_MODEL, 45))
    assert outcome.reason == REASON_ADDRESS_INVALID and outcome.stage == STAGE_CONFIG


# --- 7. 取值闸门：只有「已保存、已启用、已确认、本机」才值得发一次请求 ---

def test_no_saved_config_at_all_is_not_testable_and_sends_nothing(monkeypatch):
    factory, transport = record_text_http(monkeypatch)
    payload = post_text_probe()
    assert payload["reason"] == REASON_NOT_TESTABLE and payload["stage"] == STAGE_CONFIG
    assert payload["checked"] == {}
    assert factory.call_count == 0


@pytest.mark.parametrize("changes,expected", [
    ({"enabled": False}, (False, "local")),
    ({"execution_location": "cloud", "base_url": TEXT_BASE,
     "credential_action": "replace", "api_key": TEXT_KEY}, (True, "cloud"))])
def test_a_disabled_or_cloud_entry_is_refused_before_any_socket(monkeypatch, changes, expected):
    written = enable_local_text(**changes)
    entry = written["capabilities"]["text"]
    assert (entry["enabled"], entry["execution_location"]) == expected
    factory, transport = record_text_http(monkeypatch)
    payload = post_text_probe()
    assert payload["reason"] == REASON_NOT_TESTABLE
    assert payload["message"] == messages()[REASON_NOT_TESTABLE]
    assert factory.call_count == 0 and transport.post.call_count == 0


def test_a_needs_confirmation_entry_is_not_probed(monkeypatch):
    enable_local_text()
    stored = capability_config.read_capability_settings()
    stored["capabilities"]["text"]["needs_confirmation"] = True
    capability_config.write_capability_settings(stored)
    factory, transport = record_text_http(monkeypatch)
    payload = post_text_probe()
    assert payload["reason"] == REASON_NOT_TESTABLE
    assert factory.call_count == 0


def test_the_probe_reads_the_v1_file_never_as_a_fallback(monkeypatch):
    """探针只认 v2 的 text 条目：走到 v1 兜底那条路就该当场炸，而不是悄悄用旧密钥发一次。"""
    with pytest.raises(AssertionError):
        text_probe._no_legacy_loader()


# --- 8. 路由面：失败是数据不是异常，永远 200，永不 500 ---

def test_the_route_answers_200_even_when_the_probe_module_explodes(monkeypatch):
    enable_local_text()
    record_text_http(monkeypatch)
    monkeypatch.setattr(text_probe, "probe_local_text", Mock(side_effect=RuntimeError(SENTINEL)))
    payload = post_text_probe()
    assert payload["reason"] == REASON_UNKNOWN and payload["ok"] is False
    assert payload["message"] == messages()[REASON_UNKNOWN]
    assert SENTINEL not in json.dumps(payload, ensure_ascii=False)


def test_the_route_takes_no_parameters(monkeypatch):
    enable_local_text()
    factory, transport = record_text_http(monkeypatch)
    response = TestClient(server.app).post(TEXT_PROBE_URL, json={"foo": 1})
    assert response.status_code == 400
    assert response.json()["detail"] == "测试连接不接受任何参数"
    assert factory.call_count == 0


def test_cross_origin_text_probe_is_rejected_before_any_socket(monkeypatch):
    enable_local_text()
    factory, transport = record_text_http(monkeypatch)
    response = TestClient(server.app).post(TEXT_PROBE_URL, json={},
                                           headers={"Origin": "https://other.invalid"})
    assert response.status_code == 403
    assert factory.call_count == 0 and transport.post.call_count == 0


def test_the_probe_writes_nothing_to_the_config_file(monkeypatch):
    """「只读配置」不能靠嘴说：探测前后磁盘上那份 v2 文件必须逐字节相同。"""
    enable_local_text()
    record_text_http(monkeypatch)
    path = capability_config.capability_config_path()
    before = hashlib.md5(path.read_bytes()).hexdigest()
    post_text_probe()
    assert hashlib.md5(path.read_bytes()).hexdigest() == before, "探针改写了配置文件"


def test_the_probe_leaves_the_saved_entry_fields_untouched(monkeypatch):
    written = enable_local_text()
    record_text_http(monkeypatch)
    post_text_probe()
    public = TestClient(server.app).get("/api/ai-capabilities").json()["capabilities"]["text"]
    stored = written["capabilities"]["text"]
    for key in ("execution_location", "adapter", "base_url", "model", "timeout_seconds"):
        assert public[key] == stored[key], key
    assert public["has_api_key"] is False


def test_a_cloud_entrys_key_never_appears_in_the_not_testable_answer(monkeypatch):
    """闸门那条路会把磁盘上的 base_url 与 model 回填给界面，这里钉住它不带密钥片段。"""
    enable_text()
    record_text_http(monkeypatch)
    payload = post_text_probe()
    assert payload["reason"] == REASON_NOT_TESTABLE
    dumped = json.dumps(payload, ensure_ascii=False)
    assert_no_secret(dumped)
    assert TEXT_KEY not in dumped


def test_the_text_probe_never_opens_the_vision_route_or_touches_the_image_helpers(monkeypatch):
    from app.core import local_vision_client
    enable_local_text()
    factory, transport = record_text_http(monkeypatch)
    monkeypatch.setattr(local_vision_client, "encode_local_image",
                        Mock(side_effect=AssertionError("文字探针不得读素材图片")))
    monkeypatch.setattr(local_vision_client, "local_chat_endpoint",
                        Mock(side_effect=AssertionError("文字探针不得用图片理解的原生口地址")))
    payload = post_text_probe()
    assert payload["reason"] == REASON_PASSED
    assert transport.get.call_args.args[0] == "http://127.0.0.1:11434/api/tags"
    assert transport.post.call_args.args[0] == f"{LOCAL_TEXT_BASE}/chat/completions"
    assert PROBE_URL != TEXT_PROBE_URL


# --- 9. 前端不变量：两个能力共用一套渲染，探针这条路不许借长档 ---

def test_the_text_route_is_reachable_from_the_same_frontend_call_site():
    app_js = (server.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "test-local-connection" in app_js
    assert 'api(`/api/ai-capabilities/${aiSettingsEditor.capability}/test`' in app_js
    # 路径由当前 tab 拼出来，所以这里不许出现任何一条把能力写死的 test 调用。
    assert 'vision/test"' not in app_js and 'text/test"' not in app_js
