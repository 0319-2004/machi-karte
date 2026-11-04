"""Claude 属性推定のロジック検証（API を呼ばない。費用ゼロ）。

保証すること:
- 構造化出力の検証・正規化（null↔confidence0↔unresolvable の整合、未知コード弾き）
- パース失敗は1回だけリトライし、だめなら両属性を判断不能にする
- 入力ハッシュのキャッシュで二重に呼ばない／画像違いは別ハッシュ
- トークン使用量を複数回ぶん合算し、費用を出す

Anthropic クライアントはフェイクに差し替える（ネットワーク・課金なし）。
"""
from __future__ import annotations

import json
import os

import pytest

from estimate import claude_infer as ci

USAGE_CODES = {"401": "業務施設", "402": "商業施設", "412": "共同住宅",
               "414": "店舗等併用共同住宅", "461": "不明"}


# --- フェイク Anthropic クライアント -----------------------------------------

class _Usage:
    def __init__(self, i=100, o=20, cr=0, cc=0):
        self.input_tokens = i
        self.output_tokens = o
        self.cache_read_input_tokens = cr
        self.cache_creation_input_tokens = cc


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Msg:
    def __init__(self, text, stop_reason="end_turn", usage=None):
        self.content = [_Block(text)] if text is not None else []
        self.stop_reason = stop_reason
        self.usage = usage or _Usage()


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def _good_json(usage="401", storeys=5, unresolvable=None):
    return json.dumps({
        "usage": {"value": usage, "confidence": 0.8, "reasoning": "POIにオフィス"},
        "storeys": {"value": storeys, "confidence": 0.7, "reasoning": "高さから"},
        "unresolvable": unresolvable or [],
    })


def _cfg():
    return ci.InferConfig(model="claude-opus-4-8", prompt_text="SYS",
                          prompt_version="v1", schema=ci.build_schema(USAGE_CODES))


# --- 正規化 ------------------------------------------------------------------

def test_normalize_valid():
    raw = json.loads(_good_json())
    out = ci._normalize_result(raw, USAGE_CODES)
    assert out["usage"]["value"] == "401"
    assert out["storeys"]["value"] == 5
    assert out["unresolvable"] == []


def test_normalize_null_becomes_unresolvable():
    raw = {"usage": {"value": None, "confidence": 0.9, "reasoning": "x"},
           "storeys": {"value": 3, "confidence": 0.5, "reasoning": "y"},
           "unresolvable": []}
    out = ci._normalize_result(raw, USAGE_CODES)
    # value=null なら confidence は 0 に落とし、unresolvable に入れる
    assert out["usage"]["value"] is None
    assert out["usage"]["confidence"] == 0.0
    assert "usage" in out["unresolvable"]


def test_normalize_unresolvable_wins_over_value():
    raw = {"usage": {"value": "401", "confidence": 0.9, "reasoning": "x"},
           "storeys": {"value": 3, "confidence": 0.5, "reasoning": "y"},
           "unresolvable": ["usage"]}
    out = ci._normalize_result(raw, USAGE_CODES)
    # unresolvable に入っているなら値は採らない
    assert out["usage"]["value"] is None
    assert out["usage"]["confidence"] == 0.0


def test_normalize_unknown_usage_code_raises():
    raw = json.loads(_good_json(usage="999"))
    with pytest.raises(ci.ParseError):
        ci._normalize_result(raw, USAGE_CODES)


def test_normalize_storeys_below_one_is_null():
    raw = json.loads(_good_json(storeys=0))
    out = ci._normalize_result(raw, USAGE_CODES)
    assert out["storeys"]["value"] is None
    assert "storeys" in out["unresolvable"]


# --- 呼び出し・リトライ・フォールバック --------------------------------------

def test_call_claude_success():
    client = FakeClient([_Msg(_good_json())])
    result, usage, attempts = ci.call_claude(client, _cfg(), "ctx", [], USAGE_CODES)
    assert attempts == 1
    assert result["usage"]["value"] == "401"
    assert usage["input_tokens"] == 100


def test_call_claude_retry_then_success():
    client = FakeClient([_Msg("壊れたJSON{"), _Msg(_good_json())])
    result, usage, attempts = ci.call_claude(client, _cfg(), "ctx", [], USAGE_CODES)
    assert attempts == 2
    assert result["usage"]["value"] == "401"
    # 2回ぶんのトークンを合算
    assert usage["input_tokens"] == 200


def test_call_claude_fallback_unresolvable():
    client = FakeClient([_Msg("壊れ1{"), _Msg("壊れ2{")])
    result, usage, attempts = ci.call_claude(client, _cfg(), "ctx", [], USAGE_CODES)
    assert attempts == 2
    assert result["usage"]["value"] is None
    assert result["storeys"]["value"] is None
    assert set(result["unresolvable"]) == {"usage", "storeys"}


def test_call_claude_refusal_is_parse_failure():
    client = FakeClient([_Msg(_good_json(), stop_reason="refusal"),
                         _Msg(_good_json())])
    result, usage, attempts = ci.call_claude(client, _cfg(), "ctx", [], USAGE_CODES)
    # 1回目は refusal 扱いでリトライ、2回目で成功
    assert attempts == 2
    assert result["usage"]["value"] == "401"


# --- キャッシュ --------------------------------------------------------------

def test_cache_avoids_second_call(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "CACHE_DIR", str(tmp_path))
    b = ci.Building("13104-bldg-1", 30.0, 200.0, (0.0, 0.0))
    ctx = {"building_id": b.building_id, "height_m": 30.0, "roof_edge_area_m2": 200.0,
           "is_highrise": True, "neighbor_usage_distribution": [],
           "poi_nearby": [], "poi_categories": [],
           "tentative": {"storeys_from_height": 8, "floor_height_m": 3.8,
                         "usage_from_poi": None}}
    cfg = _cfg()

    client = FakeClient([_Msg(_good_json())])
    rec1 = ci.infer_building(client, cfg, b, ctx, [], USAGE_CODES)
    assert rec1["cached"] is False
    assert client.messages.calls == 1

    # 2回目は同じ入力ハッシュ → キャッシュから返り、API は呼ばれない
    client2 = FakeClient([])   # 呼ばれたら pop で落ちる
    rec2 = ci.infer_building(client2, cfg, b, ctx, [], USAGE_CODES)
    assert rec2["cached"] is True
    assert client2.messages.calls == 0


def test_image_changes_hash():
    cfg = _cfg()
    ctx = {"building_id": "x"}
    h0 = ci.input_hash(cfg, ctx, [])
    h1 = ci.input_hash(cfg, ctx, [ci.Image("image/jpeg", "AAAA")])
    h2 = ci.input_hash(cfg, ctx, [ci.Image("image/jpeg", "BBBB")])
    assert h0 != h1 != h2 and h0 != h2


# --- 費用計算 ----------------------------------------------------------------

def test_compute_cost_known_model():
    usage = {"input_tokens": 1_000_000, "output_tokens": 0,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    # opus 4.8 入力 $5 / 1M
    assert ci.compute_cost("claude-opus-4-8", usage) == pytest.approx(5.0)


def test_compute_cost_unknown_model_is_none():
    assert ci.compute_cost("some-unknown-model", {"input_tokens": 100}) is None


# --- 用途コード読み込み（XML があれば） --------------------------------------

def test_load_usage_codes():
    if not os.path.exists(ci.USAGE_XML):
        pytest.skip("Building_usage.xml が無い")
    codes = ci.load_usage_codes()
    assert len(codes) == 18
    assert codes["401"] == "業務施設"
    assert codes["461"] == "不明"
