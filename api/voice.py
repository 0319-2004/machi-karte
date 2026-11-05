"""音声入力（試験的）の振り分け：認識後テキストを Claude で用途・階数・特徴に分ける。

現地調査モードの「🎤 音声で入力」で、端末の音声認識が返した **テキストだけ** を受け取り、
用途コード・地上階数・特徴（メモ）に振り分ける。音声データ自体は扱わない・保存しない。

重要（docs/decisions.md に記録）：
- 読み取れない項目は null（推測で埋めない。設計書「推測で埋めない」）。
- 入力ハッシュでキャッシュし、トークン数・費用をログに残す（CLAUDE.md）。
- 音声の端末内処理は Web Speech API では保証されない。認識はブラウザ提供元
  （Google / Apple）のサーバーで行われる。UI で明示し、同意した人だけが使う。
"""
from __future__ import annotations

import hashlib
import json
import os
import time

from .db import DERIVED_DIR

CACHE_DIR = "data/cache/voice_parse"
LOG_PATH = os.path.join(DERIVED_DIR, "voice_parse_log.jsonl")

VOICE_SYSTEM = (
    "あなたは建物の現地調査の補助です。調査員が1棟の建物を見ながら話した内容の"
    "書き起こし（音声認識の結果）を受け取り、次の3つに振り分けてください。"
    "\n- usage: 建物の用途。必ず与えられた用途コード一覧のいずれかのコード（文字列）。"
    "一覧に当てはまらない・言及が無い場合は null。"
    "\n- storeys: 地上の階数（整数）。『3階建て』『5階』など明言された場合のみ。"
    "地下階・塔屋は数えない。言及が無い・曖昧なら null。"
    "\n- features: 用途・階数以外で調査メモに残すべき特徴（外観・看板・空室・工事中など）を"
    "短い日本語で。特に無ければ null。"
    "\n推測で埋めないこと。はっきり述べられていない項目は必ず null にする。"
    "聞き間違いが疑われる箇所は features に補足してよいが、usage/storeys は確実な場合だけ埋める。"
)

def build_schema(valid_codes: list[str]) -> dict:
    """構造化出力の json_schema（claude_infer と同じ output_config 方式）。"""
    return {
        "type": "object",
        "properties": {
            "usage": {"anyOf": [{"type": "string", "enum": valid_codes},
                                {"type": "null"}]},
            "storeys": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            "features": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "reasoning": {"type": "string"},
        },
        "required": ["usage", "storeys", "features", "reasoning"],
        "additionalProperties": False,
    }


def _cache_read(h: str) -> dict | None:
    path = os.path.join(CACHE_DIR, f"{h}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def _cache_write(h: str, rec: dict) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(os.path.join(CACHE_DIR, f"{h}.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)


def parse_voice(text: str, usage_codes: list[dict],
                model: str | None = None) -> dict:
    """認識テキストを振り分けた {usage, storeys, features, reasoning} を返す。

    usage は与えられた一覧のコードに限る（一覧外は null に落とす）。
    音声は受け取らない（text のみ）。入力ハッシュでキャッシュしログに残す。
    """
    import anthropic
    from dotenv import load_dotenv

    load_dotenv()
    from estimate.claude_infer import _usage_dict, compute_cost, get_model

    text = (text or "").strip()
    if not text:
        return {"usage": None, "storeys": None, "features": None,
                "reasoning": "入力テキストが空です", "cached": False}

    model = model or get_model()
    valid = {str(u["code"]) for u in usage_codes}
    codes_blob = "\n".join(f"{u['code']}: {u['label']}" for u in usage_codes)

    blob = json.dumps({"model": model, "v": 1, "codes": sorted(valid), "text": text},
                      sort_keys=True, ensure_ascii=False)
    h = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    cached = _cache_read(h)
    if cached is not None:
        return {**cached["result"], "cached": True}

    user = ("用途コード一覧:\n" + codes_blob
            + "\n\n調査員の書き起こし:\n" + text
            + "\n\n述べられていない項目は必ず null にしてください。")
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model, max_tokens=1024,
        system=[{"type": "text", "text": VOICE_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema",
                                   "schema": build_schema(sorted(valid))}},
    )
    text_out = next((b.text for b in msg.content
                     if getattr(b, "type", None) == "text"), None)
    try:
        data = json.loads(text_out) if text_out else {}
    except (json.JSONDecodeError, TypeError):
        data = {}

    usage_code = data.get("usage")
    if usage_code is not None and str(usage_code) not in valid:
        usage_code = None  # 一覧外は採らない（推測で埋めない）
    storeys = data.get("storeys")
    try:
        storeys = int(storeys) if storeys is not None else None
    except (TypeError, ValueError):
        storeys = None
    if storeys is not None and storeys < 1:
        storeys = None
    features = data.get("features") or None

    result = {
        "usage": str(usage_code) if usage_code is not None else None,
        "storeys": storeys,
        "features": features,
        "reasoning": data.get("reasoning") or "",
    }
    usage = _usage_dict(msg.usage)
    cost = compute_cost(model, usage)
    _cache_write(h, {"hash": h, "result": result, "usage": usage, "cost_usd": cost})

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "model": model, "chars": len(text),
                            "usage": usage, "cost_usd": cost},
                           ensure_ascii=False) + "\n")
    return {**result, "cached": False}
