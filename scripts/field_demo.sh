#!/usr/bin/env bash
# 現地デモ用ランチャ：Macを外に持ち出して、スマホで実機デモするための起動スクリプト。
#   1) Macをスリープさせない（caffeinate）＝蓋を開けたまま撮影中に落ちないように
#   2) FastAPI（:8000）を起動（未起動なら。--reload なしで安定重視）
#   3) cloudflared の Quick Tunnel を張り、HTTPS の公開URLを表示（qrencode があればQRも）
# Ctrl-C で、このスクリプトが起動したものだけ後片付けして終了する。
#
# 使い方：  bash scripts/field_demo.sh    （または make tunnel）
# 注意：Quick Tunnel は使い捨て。URLは起動ごとに変わる。撮影本番まで止めないこと。
set -euo pipefail

PORT=8000
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CAFFEINATE_PID=""
SERVER_PID=""      # このスクリプトが起動したときだけセット（既存サーバーは触らない）
TUNNEL_PID=""
LOGDIR="$(mktemp -d)"

cleanup() {
  echo
  echo "▶ 片付け中…"
  [ -n "$TUNNEL_PID" ] && kill "$TUNNEL_PID" 2>/dev/null || true
  [ -n "$SERVER_PID" ] && { kill "$SERVER_PID" 2>/dev/null || true; echo "  - サーバー停止（このスクリプトで起動したぶん）"; }
  [ -n "$CAFFEINATE_PID" ] && kill "$CAFFEINATE_PID" 2>/dev/null || true
  echo "  - トンネル停止（公開URLは無効になりました）"
  echo "完了。"
}
trap cleanup EXIT INT TERM

# 1) スリープ防止（-d 画面, -i システム, -m ディスク, -s AC接続時）。電源アダプタ推奨。
caffeinate -dims &
CAFFEINATE_PID=$!
echo "▶ Macのスリープを抑止中（電源アダプタをつないでおくこと）"

# 2) サーバー（未起動なら起動）
if lsof -iTCP:"$PORT" -sTCP:LISTEN -n -P >/dev/null 2>&1; then
  echo "▶ サーバーは既に :$PORT で起動済み（そのまま使う）"
else
  echo "▶ サーバーを起動中（uvicorn :$PORT）…"
  uv run uvicorn api.main:app --port "$PORT" >"$LOGDIR/server.log" 2>&1 &
  SERVER_PID=$!
  # 応答するまで待つ（最大30秒）
  for _ in $(seq 1 60); do
    if curl -s -o /dev/null "http://localhost:$PORT/survey.html"; then break; fi
    sleep 0.5
  done
  if ! curl -s -o /dev/null "http://localhost:$PORT/survey.html"; then
    echo "✗ サーバーが起動しませんでした。ログ: $LOGDIR/server.log"
    tail -20 "$LOGDIR/server.log" || true
    exit 1
  fi
  echo "  起動OK"
fi

# 3) トンネル
echo "▶ HTTPSトンネルを張っています…"
cloudflared tunnel --url "http://localhost:$PORT" --no-autoupdate >"$LOGDIR/tunnel.log" 2>&1 &
TUNNEL_PID=$!
URL=""
for _ in $(seq 1 60); do
  URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOGDIR/tunnel.log" | head -1 || true)"
  [ -n "$URL" ] && break
  sleep 0.5
done
if [ -z "$URL" ]; then
  echo "✗ トンネルのURLを取得できませんでした。ログ: $LOGDIR/tunnel.log"
  tail -20 "$LOGDIR/tunnel.log" || true
  exit 1
fi

SURVEY_URL="$URL/survey.html"
MAP_URL="$URL/map.html"
echo
echo "════════════════════════════════════════════════════════"
echo "  PC（このMac）の操作画面（絞り込み・QR発行）："
echo
echo "    $MAP_URL"
echo
echo "  スマホで直接開くなら（現地調査・「ここへ行く」）："
echo
echo "    $SURVEY_URL"
echo
echo "  トップ（デモ）：   $URL/"
echo "════════════════════════════════════════════════════════"
echo
echo "  ※ PC操作画面は必ず上の公開URL（$URL）で開くこと。"
echo "    localhost で開くと QR が localhost になりスマホで開けません。"
if command -v qrencode >/dev/null 2>&1; then
  echo
  qrencode -t ANSIUTF8 "$SURVEY_URL"
else
  echo "  （QRを出すなら: brew install qrencode ）"
fi

# PC（このMac）のブラウザを公開URLで自動で開く＝QRが自動で正しくなる（localhostで開かない）。
if command -v open >/dev/null 2>&1; then
  open "$MAP_URL" >/dev/null 2>&1 || true
  echo
  echo "▶ PCの操作画面を公開URLで開きました（$MAP_URL）"
fi
echo
echo "起動中。Ctrl-C で全部停止します。本番が終わるまで止めないこと。"
echo

# トンネルが生きている間は待機（落ちたら気づけるようフォアグラウンド待ち）
wait "$TUNNEL_PID"
