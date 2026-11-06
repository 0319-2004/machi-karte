"""音声入力（試験的）の振り分け：認識後テキストを Claude で用途・階数・特徴に分け、
足りない項目があれば現地調査員への短い質問を1つ返す（同じ API 呼び出しで済ませる）。

現地調査モードの「🎤 音声で入力」で、端末の音声認識が返した **テキストだけ** を受け取り、
用途コード・地上階数・特徴（メモ）に振り分ける。音声データ自体は扱わない・保存しない。

足りない項目を聞き返す流れ（UI がループを回す）：
- 埋まっていない項目があれば、その建物の状況に合わせた質問を1つだけ返す（全部埋まれば null）。
- 「分からない」と答えた項目は dont_know に入れ、判断不能として扱い、もう聞かない。
- 質問は1度に1つ。3往復（asked>=3）したらそれ以上は聞かない（question=null）。

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

ITEMS = ["usage", "storeys"]        # 聞き返しの対象になる項目
MAX_QUESTIONS = 3                   # 3往復したらそれ以上は聞かない
# 質問生成が外れたときの保険（項目ごとの定型質問）
CANNED_Q = {
    "usage": "この建物は何に使われていますか（1階の用途など）",
    "storeys": "地上は何階建てですか",
}

VOICE_SYSTEM = (
    "あなたは建物の現地調査の補助です。調査員が1棟の建物を見ながら話した書き起こし"
    "（音声認識の結果）と、その建物の現在の状況を受け取ります。やることは2つ。"
    "\n【1. 振り分け】"
    "\n- usage: 建物の用途。必ず与えられた用途コード一覧のいずれかのコード（文字列）。"
    "一覧に当てはまらない・言及が無い場合は null。"
    "\n- storeys: 地上の階数（整数）。『3階建て』『5階』など明言された場合のみ。"
    "地下階・塔屋は数えない。言及が無い・曖昧なら null。"
    "\n- features: 用途・階数以外で調査メモに残すべき特徴（外観・看板・空室・工事中など）。無ければ null。"
    "\n推測で埋めないこと。はっきり述べられていない項目は必ず null にする。"
    "\n1つの発話の中で同じ項目を複数回述べて食い違う場合（例：『5階、いや3階』）は、"
    "**後に述べた値**を採用する（最後の言い直しを優先）。"
    "\n【2. 聞き返し】"
    "\n- dont_know: 直前の質問に対して調査員が『分からない』『不明』など確認できない旨を"
    "答えた項目名（usage / storeys）を入れる。無ければ空配列。"
    "\n- question: まだ埋まっていない項目のうち最も重要な1つについて、その場で答えてもらう"
    "短い質問を1つだけ日本語で作る。自動推定が『判断不能』だった項目を最優先で尋ね"
    "（例：階数なら『地上は何階建てですか』）、次に信頼度が低い項目の手掛かりを聞く"
    "（例：用途なら『1階は何に使われていましたか』）。すべて埋まっている／確認済み／『分からない』"
    "と答えられた項目しか残っていなければ question は null。質問は1度に1つだけ。"
    "\n- question_target: question が尋ねている項目名（usage / storeys）。question が null なら null。"
    "\n既に値がある項目や、調査員が『分からない』と答えた項目は、二度と尋ねない。"
)


def build_schema(valid_codes: list[str]) -> dict:
    """構造化出力の json_schema（claude_infer と同じ output_config 方式）。"""
    item_enum = {"type": "string", "enum": ITEMS}
    return {
        "type": "object",
        "properties": {
            "usage": {"anyOf": [{"type": "string", "enum": valid_codes},
                                {"type": "null"}]},
            "storeys": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            "features": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "dont_know": {"type": "array", "items": item_enum},
            "question": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "question_target": {"anyOf": [item_enum, {"type": "null"}]},
            "reasoning": {"type": "string"},
        },
        "required": ["usage", "storeys", "features", "dont_know",
                     "question", "question_target", "reasoning"],
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


def _norm_status(status: dict | None) -> dict:
    """UI から来た状態を {item: {filled, value, est}} に正規化する。"""
    status = status or {}
    out = {}
    for it in ITEMS:
        s = status.get(it) or {}
        out[it] = {
            "filled": bool(s.get("filled")),
            "value": s.get("value"),
            "est": s.get("est") or "none",   # unresolvable | low | ok | none
        }
    return out


def _status_lines(status: dict, givenup: set, pending: str | None) -> str:
    est_txt = {"unresolvable": "自動推定では判断不能だった（要確認）",
               "low": "自動推定の信頼度が低い（要確認）",
               "ok": "自動推定あり", "none": "情報なし"}
    name = {"usage": "用途", "storeys": "階数"}
    lines = []
    for it in ITEMS:
        s = status[it]
        if it in givenup:
            state = "調査員が『分からない』と回答済み（もう尋ねない）"
        elif s["filled"]:
            state = f"確定済み（{s['value']}）"
        else:
            state = est_txt.get(s["est"], "情報なし")
        lines.append(f"- {name[it]}: {state}")
    pend = name.get(pending, "なし") if pending else "なし"
    return "現在の状況（この建物）:\n" + "\n".join(lines) + f"\n直前の質問: {pend}"


def parse_voice(text: str, usage_codes: list[dict], *,
                status: dict | None = None, pending: str | None = None,
                givenup: list | None = None, asked: int = 0,
                building_id: str | None = None, model: str | None = None) -> dict:
    """認識テキストを振り分け、足りなければ質問を1つ返す。

    返り値: {usage, storeys, features, dont_know, question, question_target,
             reasoning, cached}
    usage は与えられた一覧のコードに限る（一覧外は null に落とす）。
    音声は受け取らない（text のみ）。入力ハッシュでキャッシュしログに残す。
    """
    import anthropic
    from dotenv import load_dotenv

    load_dotenv()
    from estimate.claude_infer import _usage_dict, compute_cost, get_model

    text = (text or "").strip()
    status_n = _norm_status(status)
    givenup_set = {g for g in (givenup or []) if g in ITEMS}
    empty = {"usage": None, "storeys": None, "features": None, "dont_know": [],
             "question": None, "question_target": None}
    if not text:
        return {**empty, "reasoning": "入力テキストが空です", "cached": False}

    model = model or get_model()
    valid = {str(u["code"]) for u in usage_codes}
    codes_blob = "\n".join(f"{u['code']}: {u['label']}" for u in usage_codes)

    # キャッシュ键：テキスト＋状態（質問は状態に依存するため状態も含める）
    key = {"model": model, "v": 4, "codes": sorted(valid), "text": text,
           "status": status_n, "pending": pending,
           "givenup": sorted(givenup_set), "asked": int(asked)}
    blob = json.dumps(key, sort_keys=True, ensure_ascii=False)
    h = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    cached = _cache_read(h)
    if cached is not None:
        return {**cached["result"], "cached": True}

    user = ("用途コード一覧:\n" + codes_blob
            + "\n\n" + _status_lines(status_n, givenup_set, pending)
            + "\n\n調査員の書き起こし:\n" + text
            + "\n\n述べられていない項目は必ず null に。質問は未確定の項目について1つだけ。")
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
    dont_know = [i for i in (data.get("dont_know") or []) if i in ITEMS]

    # 「埋まっている」集合＝元から確定 + 今回の振り分けで入った分
    filled = {it for it in ITEMS if status_n[it]["filled"]}
    if usage_code is not None:
        filled.add("usage")
    if storeys is not None:
        filled.add("storeys")
    resolved = filled | givenup_set | set(dont_know)
    # 判断不能 > 低信頼 > その他 の順に、埋まっていない項目を並べる（判断不能を最優先で聞く）
    prio = {"unresolvable": 0, "low": 1, "ok": 2, "none": 3}
    empty_askable = sorted((i for i in ITEMS if i not in resolved),
                           key=lambda i: (prio.get(status_n[i]["est"], 3), ITEMS.index(i)))

    # 質問はサーバーが最終決定（3往復超・残りなしなら聞かない。最優先項目に揃える）
    question = data.get("question")
    target = data.get("question_target")
    if int(asked) >= MAX_QUESTIONS or not empty_askable:
        question, target = None, None
    else:
        best = empty_askable[0]
        if target != best or not question:   # Claude が最優先項目を選んでいなければ定型に落とす
            target, question = best, CANNED_Q[best]

    result = {
        "usage": str(usage_code) if usage_code is not None else None,
        "storeys": storeys,
        "features": features,
        "dont_know": dont_know,
        "question": question,
        "question_target": target,
        "reasoning": data.get("reasoning") or "",
    }
    usage = _usage_dict(msg.usage)
    cost = compute_cost(model, usage)
    _cache_write(h, {"hash": h, "result": result, "usage": usage, "cost_usd": cost})

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "building_id": building_id, "model": model,
                            "chars": len(text), "asked": int(asked),
                            "filled": sorted(filled), "gave_up": sorted(givenup_set | set(dont_know)),
                            "question_target": target,
                            "usage": usage, "cost_usd": cost},
                           ensure_ascii=False) + "\n")
    return {**result, "cached": False}
