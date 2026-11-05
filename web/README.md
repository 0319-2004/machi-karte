# web

MapLibre GL JS による地図と確認画面を置く場所。まずはビルド不要の素の HTML/JS で始める。
背景地図は国土地理院タイル（出典表示を必ず出す）。

- `index.html` … 画面の骨組み（件数カウンタ・優先度リスト・属性パネル・帰属表示リンク）。
- `app.js` … 地図（地理院 淡色＋`fill-extrusion`）、全棟⇄調査対象のみの切替アニメ、件数カウントダウン、
  リストから `flyTo`、クリックで属性パネル。API は `/api/*`、建物は `/data/buildings.geojson`。
- `style.css` … レイアウト。MapLibre GL JS は CDN（unpkg）から読み込む（ビルド不要）。

- `survey.html` / `survey.js` … 現地調査モード（スマホ前提）。地図で対象を選ぶ→近い順/優先度順の
  プラン→1棟ずつ入力。入力画面には**音声入力（試験的）**がある。

`api`（FastAPI）が `/` で配信するので、`make serve` 後に http://localhost:8000/ を開く。
現地調査モードは http://localhost:8000/survey/ 。

## 音声入力（試験的）について

現地調査の1棟入力に「🎤 音声で入力」がある。Web Speech API（`SpeechRecognition`）を使う。

- **音声は端末内では処理されない。** 認識はブラウザ提供元のサーバー（**Chrome→Google / Safari→Apple**）で
  行われる。まちカルテは**音声を保存せず、認識後の文字だけ**を扱う。初回に一度だけ同意を取り、了解した人だけが使う。
  自治体が導入を検討する際に判断できるよう、この制約を正直に記す（詳細は `docs/decisions.md`）。
- 対応：Android Chrome（実用的）、iOS Safari 14.5+（連続認識は不安定）。**Firefox は非対応**。
- 音声が非対応・拒否・失敗でも、**手入力がそのまま使え、入力は消えない**。対応端末でだけボタンが出る。
- 認識後のテキストは `POST /api/voice_parse` が Claude で用途・階数・特徴に振り分ける（読み取れない項目は空のまま）。

### 実機で試す手順

- **PC（localhost）**：HTTP でOK。http://localhost:8000/survey/ で音声まで動く。
- **スマホ実機**：マイクは secure context 必須のため、**同一Wi-Fi＋HTTP（IP直叩き）では音声は動かない**
  （地図・手入力は動く）。音声を試すには **HTTPS トンネル**が要る：
  ```
  cloudflared tunnel --url http://localhost:8000     # 表示される https URL をスマホで開く
  # または
  ngrok http 8000
  ```
  得られた `https://…/survey/` をスマホのブラウザで開き、「音声で入力」→マイク許可で確認する。
