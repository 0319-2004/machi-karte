# 判断ログ

実データで分かった事実（件数、精度、想定外）と設計判断を、日付付きで追記する。
新しいものを上に積む。

## 書き方

```
## YYYY-MM-DD タイトル

- 背景 / きっかけ：
- 判断：
- 根拠（データ・数字は再現方法も）：
- 影響範囲：
```

---

## 2026-11-08 Blender による静止画（render/）を追加

- 背景 / きっかけ：発表・README 用に、PLATEAU 新宿区の建物を Blender で描いた静止画がほしい。
- 判断：`render/extract.py` で CityGML から面を取り出し（LOD2 がある棟は LOD2、無い棟は LOD1 立体）、`render/scene.py` で Cycles（CPU）描画する。Blender は PyPI の `bpy`（5.2.3、Python 3.13 用）を別 venv に入れて使う（プロジェクトの uv 環境は 3.12 のため依存に加えない）。地面は棟の接地標高を Delaunay でつないだ簡易地形。全域カットは新宿区（13104）の棟だけを載せる。
- 根拠（`make render` で再現）：
  - 全31ファイルの抽出で 100,663 棟（うち 13104 は 57,780）、面 1,214,000。13104 の不明群（usage=461）は 4,982 で、ingest 時の検証と一致。
  - 13104 のうち LOD2 を持つ棟は 6,400（西新宿・新宿駅周辺に多い）。残りは LOD1 立体。
  - 抽出は 4 コアで約 21 秒。下見（40% 解像度・16 サンプル）は 1 枚 15〜20 秒。
  - 本番（1920×1080／真上は 1800×1800、64 サンプル＋デノイズ、CPU 4 コア）は 1 枚 3〜6 分、5 枚で約 23 分。
- 影響範囲：既存の取り込み・推定・API には触れない。画像は `data/derived/render/`（git 管理外）。

## 2026-11-07 QR を明日ノー設定で開けるように：make tunnel が公開URLでPC画面を自動起動＋現在オリジン優先

- 背景 / きっかけ：前項で「公開URLを手で貼る」運用にしたが、本番当日に手間なく開けるようにしたい。Quick Tunnel のURLは毎回変わるので、保存URLは翌日には陳腐化する。
- 判断：
  1. **`scripts/field_demo.sh`（make tunnel）が、取得した公開URLで PC の操作画面 `<tunnel>/map.html` を `open` で自動起動**。これで `location.origin` が公開URLになり、QRは設定なしで正しくなる。バナーにも「PC操作画面は必ず公開URLで開く／localhost だと QR が localhost になる」と明記。
  2. **`planBase()` を『現在のオリジンがスマホから到達可能ならそれを最優先』に変更**。トンネルで開いていれば、localStorage に前日の（死んだ）公開URLが残っていても**無視して現在のトンネルURL**で QR を作る。localhost / LAN で開いたときだけ手動設定の公開URLを使う（従来のフォールバック）。
- 動作確認（実ブラウザ/CDP＝トンネルURLで map.html を開く）：origin＝トンネル。**古い保存URLを仕込んでも** QR は現在のトンネルURLになり、警告なし・設定不要。QR の `survey.html?plan=<id>` はトンネル経由で 200。`bash -n` OK。テスト計画は `make reset-plans` で一掃（確定41件は保持）。
- 当日の手順：`make tunnel` → PC操作画面が公開URLで自動で開く → 囲んで計画 →「QRで渡す」→ スマホでQR読取 → 開く（手入力ゼロ）。
- 影響範囲：`scripts/field_demo.sh`（map.html 自動 open・バナー）、`web/app.js`（`planBase` の優先順位）、`web/README.md`。API・DB は不変。

---

## 2026-11-07 QRが localhost になりスマホで開けない問題：公開URL（ベース）を設定可能に

- 背景 / きっかけ：QR を読み込んでもスマホでページが開けない。原因は、PC の地図画面を `http://localhost:8000` で開いていたため、受け渡し URL が `location.origin`＝`localhost` になり、QR に localhost が載っていた（localhost はスマホ自身を指すので到達不可）。サーバーも cloudflared が割り当てる公開URLを知らないため自動解決できない。
- 判断：QR モーダルに**「公開URL（ベース）」入力**を追加。受け渡し URL は `planBase()`＝設定があればそれ、無ければ `location.origin`。`localhost`/ループバック/プライベートLAN（10./192.168./172.16-31.）のときは**警告**を出し、トンネルの https アドレスを貼ると QR を即再生成（`localStorage: mk.desk.planBase` に記憶）。**PC 自体をトンネルURLで開けば `location.origin` が公開URLになり設定不要**。
- 動作確認（実ブラウザ/CDP＋トンネル実アクセス）：localhost で保存→URL が localhost・警告表示。公開URL に trycloudflare を貼る→URL がトンネルに再生成・警告消え・QR再生成・記憶。トンネル経由で `survey.html?plan=<id>`・`/api/plans/<id>` とも 200。テスト計画は `make reset-plans` で一掃（確定41件は保持）。
- 影響範囲：`web/map.html`（公開URL入力・警告要素）、`web/app.js`（`planBase`/`isUnreachableOrigin`/`renderPlanQR`/`showPlanModal` 改修・入力配線）、`web/style.css`（`.pm-warn`/`.pm-base`）、`web/README.md`。API・DB は不変。

---

## 2026-11-07 現地プランのグループ分けを作り直し（上限つき）＋地名/距離表示＋ピンの重なり解消

- 背景 / きっかけ：グループ分けと訪問順が噛み合わず、密集域で「46棟が3グループ」になっていた。
- 現状確認（実データで再現）：旧 `groupMembership` は「アンカー（優先度最上位）＋アンカーから半径200m内を**全部**」でグループ化し、**棟数上限が無い**。計算順はグループ分け→グループ間順→グループ内順（順序自体は妥当）。最密の46棟で再現すると **3グループ・分布 [35, 6, 5]**（1アンカーが35棟を飲み込む。グループ直径は最大300m超）。
- 直した点：
  1. `buildGroups` に作り替え。**シード＝優先度最上位の未割当棟。そこから半径200m内を「近い順」に上限 `GROUP_MAX=10` まで**入れる（＝その場で Google ルート1本に収まる単位）。密集域でも肥大しない。同じ46棟で **5グループ・分布 [10,10,10,10,6]**、各直径120〜190mに改善。
  2. グループを先に作り、**グループ内の訪問順**（最近傍＋2-opt、入口＝直前グループの出口）と**グループ間の順番**（重心で最近傍＋2-opt、近い順）を最適化（従来の枠組みを踏襲）。
  3. **通し番号を `planNum`（planOrder の連番）に一元化**。地図ピン・一覧・入力で同じ番号を使い、グループの切れ目でも連番（飛ばない）。
  4. 10棟以下は従来どおり1グループ（1本のルート）。
- 表示：グループ見出しに **代表地名（地理院リバースジオコーダの `lv01Nm`＝町丁目「西新宿八丁目 付近」など）・棟数・グループ内の移動距離**を出す。地名はキャッシュ、取得失敗/オフラインは「グループN」にフォールバック。地理院は帰属済み（`attribution.md` に追記）。
- ピンの重なり：俯瞰地図で画素上重なるピンを**放射状に少しずらす**（`setOffset`）。`zoomend`/`moveend` で再計算し、**拡大して重ならなくなればオフセット0に戻る**（＝「拡大で離れる」と「ずらす」を両立）。
- 動作確認（実ブラウザ/CDP＋スクショ）：最密46棟→5グループ[10,10,10,10,6]、番号1..46連番、地図ピン46、見出し「西新宿八丁目 付近（10棟・グループ内 約240m）」等、ピンは放射状に散って番号が読める。コンソールエラー0。
- 影響範囲：`web/survey.js`（`buildGroups`・`buildPlan`・`renderPlanMap`/`renderPlanGroups`・`declutterPins`・`fetchAreaName`・`planNum`/`planGroupDist`・定数 `GROUP_MAX`）、`web/survey.html`（`.gh-sub` 等）、`docs/attribution.md`・`web/README.md`。API・DB は不変。

---

## 2026-11-07 計画の紫は開いた直後に出さない＋デモ初期化コマンド（make demo-reset）

- 背景 / きっかけ：localStorage に残った計画（321棟）が**地図を開いた直後から紫**で出てしまい、デモの「オレンジだけ→その場で選ぶ」流れを邪魔していた。デモ前に状態をきれいにするコマンドも欲しい。
- 判断（1：紫を出す条件）：地図の紫を、計画リスト `planSel`（トレイ/QR/localStorage）とは**別の `highlightIds`（初期値 空）**で塗るようにした。`planColorExpr` は `highlightIds` を見る。
  - **開いた直後**：`planSel` は localStorage から復元してトレイには出すが、`highlightIds` は空なので**地図は紫を出さない**（オレンジだけ）。
  - **その場の操作**（囲む/確認カードの追加/区分追加/現地確認リスト/クリア）のときだけ `highlightSelection()` が `highlightIds=planSel` に同期して紫にする。
  - **保存済み計画**：進捗一覧に「地図で見る」を追加。押すと `GET /api/plans/{id}` の棟を `highlightIds` に入れて紫で反映＋その範囲へ寄せる（`fitAndHighlight`）。選択（planSel）は変えない。
- 判断（2：初期化コマンド）：`api/schema.py` に `reset_plans()` と CLI `--reset-plans` / `--reset-demo` を追加。`--reset-demo` は確定履歴・計測＋調査計画をまとめて消す（`candidates`/`buildings` は残す）。`Makefile` に `reset-plans` を追加し、`demo-reset` を `--reset-demo` 実行に変更。localStorage の紫は画面の「クリア」で消す旨を明記。
- 動作確認：(1) 実ブラウザ/CDP＝localStorage に10棟仕込み→読込直後は紫0・トレイ10、囲むと紫27・トレイ27、クリアで0/0。進捗「地図で見る」で保存済み6棟が紫（選択は変わらず0）。コンソールエラー0。(2) DBコピーで `--reset-demo`＝plans1→0・confirmations172→0・buildings不変。ライブは `make reset-plans` でテスト計画を一掃（確定41件は保持）。
- 影響範囲：`web/app.js`（`highlightIds`・`planColorExpr`・`highlightSelection`・`fitAndHighlight`・各 mutator・進捗の「地図で見る」）、`web/style.css`（`.pl-view`）、`api/schema.py`（`reset_plans`・CLI）、`Makefile`（`reset-plans`・`demo-reset`）、`web/README.md`。確定・推定・出力の内容は不変。

---

## 2026-11-07 PCの範囲選択も「2回囲むと解除」に（スマホと同じトグル）

- 背景 / きっかけ：PC の囲み選択は追加のみで、既に紫の棟を範囲で外せなかった（個別「外す」か全体「クリア」のみ）。スマホ（survey.html）と同じく、同じ所を2度囲んだら解除したい。
- 判断：範囲選択の確定を `toggleRegionPlan(ids)` に差し替え。**囲んだ中の半数以上が既に選択済みなら、その範囲の棟をまとめて解除**、そうでなければ未選択を追加（少しズレて囲んでも確実に外せるよう半数基準＝スマホの `applyRegionSelection` と同じ）。onsite 区分の一括追加（`addManyToPlan`）は従来どおり追加専用で据え置き。ヒントに追加/解除の棟数を表示。
- 動作確認（実ブラウザ/CDP）：同じ範囲を 1回→17棟追加、2回→17棟解除（count 0）、3回→再び17棟。コンソールエラー0。
- 補足：計画選択は従来どおり localStorage に残る（再読込後も紫のまま＝QR 前に継続できる設計）。不要なら「クリア」か、もう一度囲んで解除できる。
- 影響範囲：`web/app.js`（`toggleRegionPlan`・範囲選択の確定処理）、`web/README.md`。CSS/HTML・API・DB は不変。

---

## 2026-11-07 選択色が3Dで紫に変わらない不具合を修正（重ねをやめ b-tgt 自体を塗り替え）

- 背景 / きっかけ：範囲選択しても**橙の建物が紫に変わらない**。原因は、紫の `b-plan` を橙の `b-tgt` に**同じ外形で重ねていた**ため、3D（斜め）視点では建物の側面が Z ファイティングで橙に負け、紫がほぼ見えなかった（前回の「4m高くする」策は真上の屋根だけ救い、側面は救えていなかった）。
- 判断：**別レイヤを重ねる方式をやめ、`b-tgt`（および `b-non`）自体の色を「選択中なら紫／それ以外は橙（灰）」の `case` 式で切り替える**。1レイヤの色変更なので重なりが無く、3D でも真上でも Z ファイティングが起きない。`b-plan` レイヤは削除。
  - `planColorExpr(base) = ["case", ["in",["get","id"],["literal",planSel]], COLOR_PLAN, base]`。
  - `updatePlanLayer()` は `b-tgt`/`b-non` の色式を現在の `planSel` で塗り替える。デモの抽出前（`targetsRevealed=false`）は灰色演出を壊さないよう触らない。`revealTargets()` で `targetsRevealed=true` にし、色式（橙＋選択紫）に切り替える。
- 動作確認（実ブラウザ/CDP＋スクショ画素判定）：3D（ピッチ55）で80棟選択→紫 368→2,539px・橙 753→328px（＝橙が紫に変化）。クリアで紫が消え橙に戻る。コンソールエラー0。
- 影響範囲：`web/app.js`（`b-plan` 削除、`planColorExpr`・`targetsRevealed`、`b-tgt`/`b-non` 色式、`updatePlanLayer`・`revealTargets`、クリック対象・`DEMO_OVERLAY_LAYERS` から b-plan 除外）。CSS/HTML は不変。

---

## 2026-11-07 囲み選択をタブレット対応に：視点ボタンと範囲選択ボタンを分離（Shift廃止）

- 背景 / きっかけ：範囲選択を Shift＋ドラッグにしたが、**タブレットには Shift が無い**。視点（真上）と選択を混ぜず、ボタンで切り替えたいという方針。
- 判断：`setupMapRegionSelect` を `setupMapTools` に作り替え、**2つの独立したボタン**に分ける。
  - **視点ボタン**（`#view-toggle`）：真上（ピッチ0・北上）⇄ 3D を切り替える。選択とは独立で、**OFF 中はいつでも移動・ズーム可**。保存した視点に戻す。
  - **範囲選択ボタン**（`#region-toggle`）：ON の間だけ地図ジェスチャ（dragPan/boxZoom/doubleClickZoom/touchZoomRotate/touchPitch）を止め、**ドラッグ（指でも／Shift不要）で矩形選択**。OFF で通常の地図操作に戻る。`region-on` の間だけ `cursor:crosshair`＋canvas `touch-action:none`。
- トレードオフ：選択中は移動できない（明示モード）。別エリアを見るときは「範囲選択」を一度 OFF にする。真上＋ズームアウトで広く見てから選べるので実用上の不便は小さい。Shift＋ドラッグの PC ショートカットは廃止（1モデルに統一）。
- 動作確認（実ブラウザ/CDP）：視点ボタンで 55°⇄0°・真上でもパン可・戻すと55°。範囲選択 ON で dragPan 無効＋**Shiftなしのドラッグで25棟選択**、OFF で dragPan/touchZoomRotate 復帰。コンソールエラー0。
- 影響範囲：`web/map.html`（視点ボタン追加）、`web/app.js`（`setupMapTools`）、`web/style.css`（ボタン＋region-on 復活）、`web/README.md`。API・DB は不変。

---

## 2026-11-07 囲みモードの不具合2件：選択色が出ない／移動できない を修正

- 背景 / きっかけ：(1) 囲んで選んでも**紫（選択色）が出ない**。(2) 真上に切り替わった後、**別地点を見たいのにドラッグが全部範囲選択になり移動できない**。
- 判断（1：Zファイティング）：`b-plan`（紫）は `b-tgt`（橙）と**同じ外形・同じ高さ**で、真上（ピッチ0）だと屋根が同一平面に重なり Z ファイティングで橙が勝ち、紫が見えていなかった。`b-plan` の高さを `["+",HEIGHT_EXPR,4]`（4m高く）・不透明度1にして確実に上へ出す。
- 判断（2：移動と選択の分離）：囲みモード中は **通常ドラッグ＝移動（dragPan 有効のまま）**、**Shift＋ドラッグ＝範囲選択**に変更。選択ドラッグの間だけ `dragPan` を一時停止し、終了で戻す。`boxZoom`（既定の Shift＋ドラッグ）はモード中だけ無効化して選択と衝突させない。ヒントを「通常ドラッグで移動／Shift＋ドラッグで囲んで選択（真上から）」に更新。`region-on` の `cursor:crosshair`/`touch-action:none` は移動を妨げるため撤去。
- 動作確認（実ブラウザ/CDP＋スクリーンショット画素判定）：モード中 pitch0・dragPan 有効・boxZoom 無効。通常ドラッグ→中心が動き選択は増えない。Shift＋ドラッグ→21〜23棟選択。真上スクショで**紫 約1,587px（橙 約585px）＝選択棟の紫が視認できる**。選択後に dragPan 復帰。コンソールエラー0。
- 影響範囲：`web/app.js`（`b-plan` の高さ・`setupMapRegionSelect` の setMode/pointer）、`web/style.css`（region-on の cursor/touch-action 撤去）、`web/README.md`。API・DB は不変。

---

## 2026-11-07 囲みモード：真上からの俯瞰にギュインと切り替え

- 背景 / きっかけ：PC の「囲んで計画に追加」は 3D 俯瞰（ピッチ55・斜め）のままだと矩形で囲みにくい。真上から見た方が範囲選択が直感的。
- 判断：囲みモードに入ったら `easeTo({pitch:0,bearing:0,duration:650})`（easeOut）で**真上・北上へギュイン**と切り替える。入る直前のピッチ・ベアリングを控え、**解除時に元の3D視点へ戻す**。中心・ズームは保持。
- 動作確認（実ブラウザ/CDP）：入前[55,-20]→モード中[0,0]→解除後[55,-20]。コンソールエラー0。
- 影響範囲：`web/app.js`（`setupMapRegionSelect` の `setMode`）のみ。

---

## 2026-11-07 調査計画トレイに「クリア（すべて解除）」を追加

- 背景 / きっかけ：PC で棟を計画に入れると、個別の「外す」はあるが**一括リセットが無く**、実質「QRで渡す」しか出口が無かった。
- 判断：計画トレイ見出しに「クリア」ボタン（計画が0件のときは非表示）を追加。押すと `planSel` を空にし、`localStorage`（`mk.survey.selected`）も `[]`、地図の紫ハイライト（`b-plan`）も消える。確認カードを開いていれば「調査計画に追加」ボタンの表示も戻す。スマホ側は既存の「すべて解除」で対応済みのため PC のみ追加。伝播を止めて一覧の開閉とは独立に動かす。
- 動作確認（実ブラウザ/CDP）：囲んで118棟→「クリア」→ 0件・トレイ empty・保存無効・`localStorage=[]`・紫フィルタ空。コンソールエラー0。
- 影響範囲：`web/map.html`（見出しにボタン）、`web/style.css`（スタイル）、`web/app.js`（`clearPlan`・`renderPlanTray` の表示制御）、`web/README.md`。API・DB は不変。

---

## 2026-11-07 現地プラン：少数（10棟以下）は距離でグループ分けしない

- 背景 / きっかけ：4棟しか選んでいないのにグループ化される、という指摘。従来のグループ分けは**距離（半径200m）だけ**が基準で**棟数のしきい値が無かった**ため、離れた4棟は4グループ（各1棟）に分かれていた。
- 判断：**選択が NAV_MAX_STOPS（=10）以下なら距離で分けず1グループにまとめる**。Google マップへ一度に渡せる上限（10地点）と揃えたので、少数は1本のルートで全棟を順に回れる。**11棟以上は従来どおり 200m でグループ化**（街区単位で回る用途・Google の上限対策）。`buildPlan` で `byPriority.length <= NAV_MAX_STOPS ? [全部] : groupMembership(...)` に分岐。グループ内の訪問順最適化（NN+2-opt）・番号付け・俯瞰地図・経路は不変。
- 根拠（実ブラウザ/CDP）：選択4→1グループ(4棟)、10→1グループ(10棟)、15→10グループ(15棟)。コンソールエラー0。
- 影響範囲：`web/survey.js`（`buildPlan` のグループ分岐）、`web/README.md`。API・DB は不変。

---

## 2026-11-07 外部地図に棟名は出せないため、プラン画面に「番号順で渡す」注記を追加

- 背景 / きっかけ：「Google マップ側に現地調査UIの名前（建物ID・番号）を一致させたい」という要望。だが Google マップのルートURL（`api=1`）は経由地に**独自ラベルを付けられず**、座標か住所/地名/Place ID しか受け取れない。まちカルテは住所を持たず、Google Places も使わない方針のため、ピンに棟名・番号は出せない。
- 判断：A 案を採用。**名前の一致は諦め、順番で対応する**。Google の停車順＝アプリの番号順である（検証済み）ことを使い、プラン画面に常時の注記（`.plan-navnote`）を1行追加：「このグループへ行く」は Google マップに一覧の番号順で渡す／ピンに番号・建物IDは出ないので番号を照らして回る旨。タップ時の `alert()`＋`window.open` は**ポップアップがブロックされる恐れ**があるため避け、静的な注記にした（出発前に番号付き一覧・ピンで並びを確認できる）。
- 動作確認（実ブラウザ/CDP）：プラン画面で `.plan-navnote` が表示され文言も確認。コンソールエラー0。
- 影響範囲：`web/survey.html`（注記の要素＋スタイル）のみ。JS・API・DB は不変。

---

## 2026-11-07 外部地図を Google マップに統一（Apple マップの複数目的地・不安定を回避）

- 背景 / きっかけ：現地調査の「ここへ行く」で、グループ（複数棟）を Google マップに渡すと複数経由地がきれいに出るが、**Apple マップだとうまくいかないことがある**（URLスキームが複数目的地に非対応で、1棟でも挙動が不安定なことがある）。
- 判断：**端末を問わず Google マップに統一**。`navDirectionsUrl` の iOS→Apple マップ分岐を外し、単一・グループとも `https://www.google.com/maps/dir/?api=1&...&travelmode=walking` に統一。Google マップ未インストールの iOS でも Safari で地図が開く（止まらない）。`isIOS` は音声入力（Web Speech/OS口述）の分岐でのみ引き続き使用。
- 制約の遵守：出発地（現在地）は渡さず外部アプリ任せ（位置を保存しない方針は不変）。アプリ内の OSRM 経路線は別機能で不変。これは 2026-11-07「現地調査に『ここへ行く』」の iOS=Apple／他=Google の分岐を上書きする。
- 動作確認：`navDirectionsUrl` が常に Google の URL を返すこと、`maps.apple` 参照が survey.js から無くなったことを確認（`node --check` OK）。
- 影響範囲：`web/survey.js`（`navDirectionsUrl`・関連コメント）のみ。

## 2026-11-07 デモ：抽出前にハイライト（計画の紫・確定の緑・写真の点）が見えていたのを修正

- 背景 / きっかけ：配布デモ（map.html, body.demo）で「街名入力→全棟灰色→抽出で橙が浮かぶ」演出の**抽出前（段階0/1）から、計画の紫（`b-plan`）・現地確定の緑（`b-done-onsite`）・写真のシアン点（`b-photo`）が見えていた**。これらはロード時にフィルタが入る（`planSel`／`loadConfirmed`／`loadPhotos`）ため、全棟灰色の演出が崩れていた。
- 判断：デモのときは**抽出まで隠す**。`DEMO_OVERLAY_LAYERS = [b-plan, b-onsite, b-done-onsite, b-sel, b-photo]` を定義し、`addBuildingLayers` の最後で `DEMO` なら `visibility:none` に。抽出（`window.MK.revealTargets`）で `visibility:visible` に戻す（既存の b-tgt を橙に出すのと同じタイミング）。`b-non`/`b-tgt` は従来どおり opacity で制御（変更なし）。非デモ時は常に表示（従来どおり）。
- 根拠（実ブラウザ＝ヘッドレス Chrome / CDP）：段階0・段階1では5レイヤとも `visibility=none`・`queryRenderedFeatures=0`（緑/紫/点は出ない）。通し操作（新宿入力→段階1→抽出→段階2）で、抽出後に `b-plan`・`b-done-onsite` が `visible` になり、計画の紫10棟が描画。コンソールエラー0。
- 影響範囲：`web/app.js`（`DEMO_OVERLAY_LAYERS`・`addBuildingLayers` の隠し・`revealTargets` の表示）のみ。API・DB・現地調査（survey）は不変。

## 2026-11-07 PC：計画選択を地図で紫ハイライト／スマホ：グループの外部ルートに全棟を渡す

- 背景 / きっかけ：(1) PC で囲んで計画に入れても、地図上でどれを選んだか色で分からなかった。(2) スマホの「このグループへ行く」が外部地図アプリにグループ内の **1棟（先頭）しか渡していなかった**（`goTo(grp[0])`）。グループの全棟を回れるようにしたい。
- 判断（1：選択の可視化）：建物レイヤに `b-plan`（`fill-extrusion`・紫 `#b06bff`・不透明度0.95）を追加し、`b-tgt`（橙）の直上に重ねる。フィルタは計画選択 `planSel` の建物ID（`["in",["get","id"],["literal",planSel]]`）。`renderPlanTray()` のたびに `updatePlanLayer()` で `setFilter` し、囲む／確認カード／区分追加のどの経路でも即座に紫になる。クリック対象レイヤにも `b-plan` を含める。現地確認フォーカス（`b-onsite` 濃い紫 `#8e24aa`）・確定（`b-done-onsite` 緑）・選択中（`b-sel` 黄）は従来どおり上に出す。
- 判断（2：グループの全棟を外部地図へ）：グループの「ここへ行く」を `goToGroupIds(grp)` に変更し、**訪問順の全棟を複数経由地**として外部地図へ渡す（`navGroupUrl`）。
  - **グループは端末を問わず Google マップ**（`/maps/dir/?api=1&destination=<最後>&waypoints=<先頭..最後の手前>|...&travelmode=walking`）。理由：Apple マップの URL スキームは**複数目的地に非対応**で、複数地点を確実に表示できるのは Google マップのため。出発地は省略（外部アプリの現在地に任せる＝現在地は渡さない方針を維持）。1棟のときは従来どおり端末別（iOS=Apple／他=Google）。
  - **経由地の上限**：URL が長すぎると弾かれるため `NAV_MAX_STOPS=10`（経由地＋目的地）で安全側に制限。超えるグループは訪問順の先頭から10地点を渡す（同じ200mグループ内なので、現地到着後は残りをアプリ内のプランで辿れる）。全部は入らない旨はコードにコメント。
- 制約の遵守：現在地は外部アプリ側が扱い、こちらでは渡さない・保存しない（従来方針）。アプリ内に経路線は OSRM で引く既存機能は不変。
- 動作確認（localhost、実ブラウザ＝ヘッドレス Chrome を CDP で駆動。WebGL は SwiftShader）：
  - PC：段階2で囲む→計画に入った棟が `b-plan` で描画（ビューポート内で複数棟が紫）。`getFilter('b-plan')` に選択IDが入り、`queryRenderedFeatures({layers:['b-plan']})` が正の件数。コンソールエラー0。
  - スマホ：25棟のグループで「このグループへ行く」→ Google マップ URL に **経由地24＋目的地1＝25地点**（上限緩め時）を訪問順・徒歩で確認。上限適用後は10地点に制限。1棟グループは単一目的地。コンソールエラー0。
- 影響範囲：`web/app.js`（`COLOR_PLAN`・`b-plan` レイヤ・クリック対象・`updatePlanLayer`・`renderPlanTray`）、`web/survey.js`（`navGroupUrl`・`goToGroupIds`・`NAV_MAX_STOPS`・グループ見出しの配線）、`web/README.md`。API・DB は不変。

## 2026-11-07 調査計画の受け渡し（QRコード）と共有ボタン（Web Share API）

- 背景 / きっかけ：PC で絞り込んだ棟を現地スマホへ渡す導線が無く、スマホ側で選び直していた。また出力（CSV/GeoJSON/レポート）を人に渡すのが「ダウンロードしてから手作業」だった。この2つを埋める。
- 判断（1：調査計画の受け渡し）：
  - **保存**：`survey_plans`（`plan_id` / `version` / `title` / `building_ids VARCHAR[]` / `created_at` / `created_by`）を新設し、`POST /api/plans` で保存。実在する建物IDだけに絞り、重複は入力順を保って除く（1件も残らなければ 400）。`plan_id` は `secrets.token_hex(4)`（8桁、衝突時は引き直し）。
  - **進捗は状態を持たない**：`survey_plans` に done 等を持たず、`GET /api/plans`（一覧）/`GET /api/plans/{id}`（個別）で **`confirmations` との突き合わせ**で都度数える（現地 onsite・机上 desk どちらの確定でも「調査済」）。二重管理・不整合を避けるため。
  - **受け渡しURLはフロントで作る**：サーバーは `plan_id` だけ返し、ブラウザが `location.origin + "/survey.html?plan=" + id` を組み立てる。LAN/トンネル越しでも、PC がいま使っているオリジンをそのままスマホへ渡せる（サーバーは自分の外部ホスト名を知らないため）。
  - **QR はローカルライブラリ**：`qrcode-generator`（MIT, © Kazuhiko Arase）を `web/vendor/qrcode/qrcode.js` に同梱（約56KB）。CDN 非依存（デモ中に通信が不安定でも動く＝MediaPipe と同方針）。QR には**受け渡しURL（計画IDのみ）**を載せ、建物属性や位置情報は載せない。
  - **選択は既存の localStorage に合流**：PC 側の計画選択は現地調査モードと同じ `mk.survey.selected` に持つ。確認カードの「＋ 調査計画に追加」、現地確認パネルの各区分「＋ この区分を調査計画に追加」、低確度ダイアログの「現地確認リストに追加」がすべて同じトレイ（左下「📋 調査計画」）に溜まる。
  - **スマホ側**：`survey.html?plan=<id>` で `GET /api/plans/{id}` を読み、計画の棟（対象に実在するものだけ）を選択に入れて**そのまま調査プランを開く**。計画名・進捗（N/M 調査済み）をバナー表示。サーバーの `done_ids` と端末の下書き done の **OR** で「調査済」を判定。**計画指定が無ければ従来どおり**「地図で選ぶ」から始まる（スマホだけで選ぶ流れは不変）。
- 判断（2：共有ボタン）：更新候補のダウンロード（CSV/GeoJSON/レポート）の横に「共有」を追加。`navigator.share({files})` で **CSV と GeoJSON のファイルそのもの**を共有する。**`navigator.canShare({files})` が真の端末でだけボタンを出す**（非対応はダウンロードのみ）。複数ファイル不可なら CSV のみにフォールバック、ユーザーが閉じた（AbortError）ときは無言。`map.html`・`survey.html` の両方に置いた。
- 制約の遵守：QR ライブラリは MIT（`docs/licenses.md`・`docs/attribution.md`・`web/vendor/qrcode/README.md` に帰属）。QR・URL に位置情報や属性を載せない。公式データ非上書きの原則は不変（計画は候補・確定に触れない）。
- 動作確認（localhost、実ブラウザ＝ヘッドレス Chrome を CDP で駆動。WebGL は SwiftShader）：
  - PC：計画トレイに2棟→「QRで渡す」→ `plan-modal` が開き URL `…/survey.html?plan=<id>`・**QR の SVG**・タイトル表示。「進捗」で一覧（`0 / 2 棟`）。共有ボタンはこの Chrome で表示（canShare=真）。アプリ由来のコンソールエラー 0。
  - スマホ：`survey.html?plan=<id>` で**調査プランに自動遷移**、バナー「📋 …（計画 2棟）　0 / 2 棟 調査済み」、グループに2棟。`mk.survey.selected` に2棟。計画指定なしでは従来どおり「地図で選ぶ」から開始。
  - 往復：`POST /api/survey`（1棟 onsite 確定）→ `GET /api/plans/{id}` が `done 1/2`・`done_ids` に反映。無効IDの除外（3/4）・空body 400・未知計画 404 も確認。
  - 検証で作った計画・確定はライブ DB から削除して元に戻した（desk 確定 41 件・対象棟は不変）。サーバーは `make serve`（uvicorn --reload）で再起動済み。
- 影響範囲：`api/schema.py`（`survey_plans` 追加）、`api/main.py`（`/api/plans` 3本・`_plan_done_ids`・`_new_plan_id`）、`web/map.html`・`web/app.js`・`web/style.css`（計画トレイ・QR/進捗モーダル・共有）、`web/survey.html`・`web/survey.js`（計画読み込み・バナー・共有）、`web/vendor/qrcode/`（新規・MIT）、`docs/attribution.md`・`docs/licenses.md`・`web/README.md`。既存の推定・確定・出力・計測は不変。

## 2026-11-07 Phase 10 評価レポート：make eval で docs/results.md を再生成

- 背景 / きっかけ：デモと README に載せる数字を、推測なし・出典つきで1枚にまとめ、再現可能にしたい。
- 判断：`eval/results.py`（`make eval` の最後に実行）で `docs/results.md` を生成。数字は2系統に分ける。
  1. **再計算**（毎回 DB から）：削減率（`ingest.pipeline.summarize`）、階数精度（`estimate.storeys.evaluate_storeys`）、用途ベースライン（`eval.baselines.run_bands`）、現地確認棟数（`candidates.unresolvable`）、処理時間（`confirmations` を `/api/metrics` と同式で）。
  2. **記録値**（課金を伴う一度きりの実測＝再計算しない。出典の日付・再現コマンドを表に併記）：Claude 用途・階数検証の判断不能率・断定誤0件・近傍(c)との差・各費用（decisions.md 2026-11-04/05）。全棟推定費用は `data/derived/claude_infer_log.jsonl` があれば集計、無ければ「未実施」と明記。
- 付随の小改修：`eval/holdout.py` の接続を `DATA_DIR` 対応に（`api/db.py` と整合。全 eval が共有）。`eval/baselines.run_bands(con=...)` で接続を外から渡せるように（レポート生成で1接続に束ねる。渡した con は閉じない）。
- 根拠：生成値が decisions.md の実測と一致することを確認（削減 91.4%、階数 不明群重み 69.5%/95.5%、用途 不明群重み a52.6/b60.9/c62.3、現地確認 521）。
- 影響範囲：`eval/results.py`（新規）、`docs/results.md`（生成物）、`Makefile`（eval に1行追加）、`eval/holdout.py`・`eval/baselines.py`（小改修）、`README.md`（評価節を追加）。API・DB スキーマは不変。

## 2026-11-07 フロントを「ランディング＋地図アプリ」に分割（デモ演出を入り口に）

- 背景 / きっかけ：従来は入口がいきなり地図アプリ（`index.html`）だった。デモで見せたい「57,780→4,982 に減る瞬間」を入口で演出したく、トップページを分けた。
- 判断：`index.html` を**ランディング（デモ演出）**にし、地図アプリは `map.html` に移設（ランディングから `/map.html` へ）。演出用に新宿区の輪郭 `web/shinjuku_outline.geojson` を `api/build_outline.py`（PLATEAU 代表点の凹包、表示専用）で生成し配信。`web/demo.js` が段階進行（街名入力→全棟灰→抽出で対象が橙に）を担う。暗いテーマ（kepler 調）に統一。件数は `/api/stats` から取得しコード直書きしない（設計書の約束どおり）。
- 根拠：設計書「優先するもの＝デモで見せたい2つ（削減の瞬間／1棟の処理時間）」。輪郭は外部行政界を使わず PLATEAU 由来のみ（権利の確認できる素材だけ）。
- 影響範囲：`web/index.html`（新規ランディング）・`web/map.html`（旧地図アプリを移設）・`web/app.js`・`web/style.css`・`web/demo.js`・`web/README.md`・`web/shinjuku_outline.geojson`・`api/build_outline.py`。サーバーは `web/` を静的配信するため API 変更は不要。現地調査 `survey.html` は別導線で不変。

## 2026-11-07 Mapillary を使用しない決定（被覆率不足で実装に至らず、規約の制約も外れる）

- 背景 / きっかけ：現地調査で「アプリ内に徒歩経路を描きたい」という要望が出た。だが設計書の禁止事項（§5＝Mapillary 規約由来）が「ナビ・経路案内をツールに組み込まない」と定めており、経路描画と抵触する。そこで、そもそも Mapillary が稼働コードでどこまで使われているかを棚卸しした（2026-11-07 の調査）。
- 調査結果（実装を変えずに確認）：
  - 優先度スコアの `has_image` は **LOD2テクスチャ**（`b.lod_type='LOD2'`）であって Mapillary ではない（重み0.05）。Mapillary の重みは実質0。
  - `estimate/claude_infer.py` の既定 `images_provider=no_images` のため、Claude に渡る画像は **全4,982棟で0枚**。Mapillary 画像は推定に一度も渡っていない。
  - `estimate/mapillary_coverage.py` は**被覆率を測る一発スクリプト**で、どの稼働モジュールからも import されず、Makefile パイプライン外、出力 `data/derived/mapillary_coverage.json` を読むコードも無い（＝休眠）。
  - 画面に Mapillary 画像・ロゴは表示していない（出るのは「LOD2」バッジ）。
- 判断：**Mapillary を使用しない。** 2026-11-03 の「画像は後回し」判断（被覆が 20m以内22.9%／40m以内47.1%、向き未考慮で実際に写っている保証なし）を踏まえ、不十分なまま実装に進めるより、供給源から外す。これにより §5 由来の「ナビ・経路案内の禁止」制約も外れ、現地調査の**徒歩経路（OSRM）をアプリ内に出せる**ようになる（別エントリ参照）。
- 実施（撤去）：`estimate/mapillary_coverage.py` と `data/derived/mapillary_coverage.json` を削除。`.env`/`.env.example` の `MAPILLARY_ACCESS_TOKEN` 行を削除。帰属・設計・README・CLAUDE.md・export から Mapillary 表記を削除し、`design.md` の「Mapillaryの禁止事項」節（ナビ・経路案内の禁止）も削除。優先度の `has_image`＝LOD2 はそのまま（Mapillary ではないため並びに影響なし）。
- 影響範囲：優先度の並びは**変化なし**（Mapillary は式に入っていない）。機能上の穴なし（推定は従来どおり画像なしで回る）。失われるのは休眠中の被覆測定能力のみ。
  - 注：本ログの過去エントリ（2026-11-03 の測定記録など）は履歴として残す。この決定が以後を上書きする。`まちカルテ Claude Code プロンプト集.md`（計画メモ）も履歴として残置。

## 2026-11-07 現地調査に「ここへ行く」（外部地図アプリで徒歩ルート）を追加

- 背景 / きっかけ：現地調査（スマホ）で、選んだ棟やグループへ実際にどう行くかの導線が無かった。経路案内そのものは作らない方針なので、ルート描画は端末の地図アプリに委ねる。
- 判断：目的地（建物の重心 lat/lon）だけを外部地図アプリに渡して徒歩ルートを開く「ここへ行く」を、①1棟の入力画面（ミニ地図上のボタン）②俯瞰地図のピン（タップでポップアップ→ボタン）③グループ単位（グループ見出しの「このグループへ行く」＝訪問順の先頭棟へ）に置いた。
  - 分岐：iOS は Apple マップ `https://maps.apple.com/?daddr=<lat>,<lon>&dirflg=w`、それ以外は Google マップ `https://www.google.com/maps/dir/?api=1&destination=<lat>,<lon>&travelmode=walking`。端末判定は既存の `isIOS`（iPhone/iPad/iPod・iPadOS 対応）を流用。
  - `window.open(url, "_blank", "noopener")` で新しいタブ/外部アプリに開く。調査画面の入力は消えない（下書きは入力のたび localStorage 保存済み）。
- 制約の遵守（設計書「作らないもの：ナビ・経路案内」「位置情報を保存しない」）：
  - アプリ内に経路は描かない。外部アプリに目的地を渡すだけ。
  - 出発地（現在地）は渡さない。現在地は外部アプリ側（Apple/Google）が扱う。こちらでは位置情報を保存しない（近い順ソート用の `userPos` はメモリ上のみで、ルートには渡さない）。
- 訪問順との関係：既存の訪問順（最近傍＋2-opt のグループ内順）はそのまま。グループの「ここへ行く」は `planGroups[gi][0]`（そのグループの訪問順の先頭）を目的地にする。
- 影響範囲：`web/survey.js`（`navDirectionsUrl`/`goTo`/`openPlanPopup`、ピン click、グループ見出しボタン、入力画面ボタン）と `web/survey.html`（ミニ地図ボタン・ポップアップのダークスタイル）。サーバー・DB・API は変更なし。

## 2026-11-06 音声入力 iOS：2回目問題を OS 標準の口述入力で解決＋言い直しを訂正として採用

- 背景 / きっかけ：iPhone 実機で「2回目の音声が全く記録されない」。`new SpeechRecognition()` を毎回作ると2回目以降 `onstart` は来るが `onresult` が来ず（iOS Safari の既知バグ）、`voiceFinal` 空のまま `finishVoice` の `!text` に落ちる。Chrome には無い不具合で見逃されていた。マイク許可は無関係（許可なら1回目から `not-allowed`）。
- 切り分けの経緯（実機で順に検証）：
  1. **使い回しに戻す**（`makeRecog`→`ensureRecog`、abort＋作り直しを廃止、`continuous=false` 維持）→ iOS は2回目「聞き取り中」のまま `onresult` 来ず、ダメ。
  2. **getUserMedia プライミング**（2回目開始前にマイクを一瞬掴んで解放し録音セッションを作り直す）→ iOS でやはり改善せず。WebKit の制約と判断。
  3. **採用＝B：iOS は Web Speech をやめ、OS 標準の音声入力（キーボードの🎤）** に寄せる。メモ欄に口述→「✓ 話し終えたら振り分け」で、開始時メモとの差分（新規発話ぶん）だけを `/api/voice_parse` に渡す。`isIOS` で分岐し、**Android/PC は従来の Web Speech**（1インスタンス使い回し＋プライミング）のまま。→ 実機で2回目以降も安定して振り分けOK。
- 追加で判明した別問題（ログ＋`data/cache/voice_parse` で特定）：2回目が訂正でも効かない。Claude が「確定済みの項目と食い違う新しい値」を誤認識とみなし null にしていた（例：確定5階に「35階建て」→null）。
  - 判断：**はっきり言い直された usage/storeys は、確定済みと違っても訂正として採用**するようプロンプトに明記（曖昧・明らかな誤認識は従来どおり推測せず null）。人は結局「反映」を押して確定するので勝手な上書きにはならない。プロンプト文はキャッシュ键に含まれないため **キャッシュ版 v4→v5** に更新。
  - フロント：用途・階数に当てはまらなかったとき「確認完了」ではなく「用途・階数には反映できませんでした。話した内容はメモに残しています」と表示（無視されたように見える問題）。
- トレードオフ：iOS ではキーボードが一度出る分 UX は落ちる（OS 制約として許容）。
- 動作確認：実機 iPhone で2回目以降の口述＋言い直しの反映OK。ローカルで訂正テスト（確定3階→「やっぱり5階建てだった」→ storeys=5、reasoning も「訂正とみなし」）。`node --check` / `py_compile` OK。
- 影響範囲：`web/survey.js`（音声まわり）、`api/voice.py`（プロンプト・キャッシュ版）。手入力・`/api/survey`・非対応端末は不変。

## 2026-11-06 音声入力 iOS 対策：毎回つくり直し＋1発話ごと＋エラーを画面表示

- 背景 / きっかけ：iPhone 実機で音声が「入力すら記録されない」。インスタンス使い回しに変えたら悪化した（＝使い回しは不可）。実機で再現できないため、原因を画面に出して切り分ける方針に。
- 判断：
  - **毎回 `new` し直す**（使い回しをやめる）。前インスタンスはハンドラを外してから `abort()`（古い onend の誤発火防止）。
  - **`continuous=false`**（1発話ごと。iOS は continuous＋自動再開が不安定なため）。
  - **状況とエラーを `#voice-status` にそのまま表示**：起動中／聞き取り中／聞き取り途中テキスト／`service-not-allowed`（音声入力オフ）・`not-allowed`（マイク不許可）・`network` 等を日本語で具体表示。`voiceErrored` で finishVoice がメッセージを消さないようにした。
  - 最有力の原因候補は iOS の「音声入力（Dictation）」オフ（`service-not-allowed`）。画面に設定箇所を案内。
- 動作確認：localhost 実ブラウザ（Chrome）で回帰OK（2発話とも記録・後勝ち5）。iOS は実機で `#voice-status` の表示を確認してもらって切り分ける。
- 影響範囲：`web/survey.js` の音声まわりのみ。手入力・他機能は不変。

## 2026-11-06 範囲選択に「なぞる」追加・2度囲むと解除・レポートの文字化け修正

- 背景 / きっかけ：iPhone 実機の追加要望。(1) 同じ所を2度囲んだら解除したい (2) 四角に加え「鉛筆（なぞる）」が欲しい (3) Claude 生成のレポートがスマホで文字化け。
- 修正：
  1. **2度囲むと解除**：囲んだ中の棟の **半数以上がすでに選択済みなら、まとめて解除**（未満なら追加）。半数基準にしたのは、2度目に少しズレて囲んでも確実に解除できるようにするため。四角・なぞる共通（`applyRegionSelection`）。
  2. **なぞる（フリーハンド）選択**：四角（範囲）に加え「✎ なぞる」モードを追加。指でなぞった軌跡を多角形にし、**点がその中にあるか（ray-casting）** で選ぶ。軌跡はオーバーレイ canvas（`#lasso-canvas`）に描画。両モードは排他。
  3. **ツールバーのタップ素通し**：選択モード中は `mapwrap` の pointerdown が pointer capture するため、**ツールバーのボタンを押すとモード切替できない**不具合があった。pointerdown が `.pick-tools`/`#pick-info` 上なら選択を始めず素通しするよう修正。
  4. **レポートの文字化け**：`/api/report` の Markdown に **UTF-8 BOM** を付与。スマホで保存して開くと HTTP の charset が効かず別コードと誤認するため（CSV と同じ対処）。
- 動作確認（localhost 実ブラウザ）：四角 0→44→0（再度囲むと解除）、なぞる 0→12→0、モード排他、ツールバー切替、レポート先頭に BOM(efbbbf)。アプリ由来のコンソールエラー無し。
- 影響範囲：`web/survey.html`・`web/survey.js`（選択まわり）、`api/main.py`（レポートの BOM）。

## 2026-11-06 iPhone 実機フィードバックの修正（範囲選択・音声2回目・スマホ出力・地図/リスト切替）

- 背景 / きっかけ：cloudflared トンネルで iPhone 実機テスト。4点の不具合・要望が出た。
- 修正：
  1. **範囲で選択がスマホで効かない**（マウスのドラッグ前提だった）：範囲モード中は `.mapwrap` に `touch-action:none` を付け、`dragPan`/`touchZoomRotate`/`touchPitch`/`doubleClickZoom` を全部止め、`setPointerCapture` で指が外れても追従。→ ドラッグで矩形選択が動く（ブラウザ検証：ドラッグで0→64棟）。
  2. **音声の2回目が記録されない**（iOS Safari は `new SpeechRecognition()` を毎回作ると2回目以降 `onresult` が来ない既知不具合）：**1インスタンスを使い回す**（`ensureRecog`）。→ 2回続けて発話しても両方記録、後勝ちも維持（「3階」→「やっぱり5階」→5）。
  3. **スマホから直接 CSV/GeoJSON を出力したい**：現地調査画面の上部に「⬇ 出力」を追加し、CSV/GeoJSON ダウンロードとレポート生成（`POST /api/report`）をパネルから実行。確定0件なら無効表示。
  4. **「地図で選ぶ」に戻れない**（トグルが常に「リスト」表示で戻り先が不明だった）：ボタンのラベルを **「リスト」⇄「地図」** に切替え、地図に戻るとき `pickMap.resize()` で描き直す。
- 動作確認（localhost 実ブラウザ＝Playwright＋Chrome。音声は疑似実装、タッチ固有部は touch-action/capture の付与で対応）：上記4点すべて期待どおり。アプリ由来のコンソールエラー無し（favicon 404 のみ）。
- 影響範囲：`web/survey.html`・`web/survey.js` のみ。推定・API・出力の内容は不変。画像処理（顔ぼかし＋手塗り）は実機でも良好との評価。

## 2026-11-06 MediaPipe をローカル配信に（CDN 依存をやめる）＋通し動作確認

- 背景 / きっかけ：顔ぼかしのモデル/ローダ/WASM を CDN から読んでいた。デモ中に通信が不安定でも動くよう、自サーバーから配信する。
- サイズ実測（`@mediapipe/tasks-vision@0.10.18`）：モデル `blaze_face_short_range.tflite` **約224KB**、`vision_bundle.mjs` 約137KB、WASM ローダ `.js` 各約200KB、**WASM 本体 `vision_wasm_internal.wasm` 約9.1MB／`nosimd` 約8.9MB**（合計約20MB）。
- 判断：
  - **小さいもの（モデル＋ローダ、合計約760KB）はリポジトリに入れる**（`web/vendor/mediapipe/`：`blaze_face_short_range.tflite`・`vision_bundle.mjs`・`wasm/*.js`）。ライセンスは **Apache-2.0**（© The MediaPipe Authors）で MIT・ODbL・CC BY-SA と整合。
  - **大きい WASM（各約9MB）は git 管理外**（`.gitignore` に `web/vendor/mediapipe/wasm/*.wasm`）。`make fetch-mediapipe` で取得（CLAUDE.md「大きいデータはリポジトリに入れない」に沿う）。
  - `web/survey.js` は CDN ではなく `/vendor/mediapipe/`（アプリの root 配信）から `import()`／WASM／モデルを読む。`.mjs`→`text/javascript`・`.wasm`→`application/wasm` は Python 3.12 の mimetypes が既定で返すため追加設定不要。
  - 帰属：`docs/attribution.md` を「自サーバー配信」に更新、`web/vendor/mediapipe/README.md` にライセンス・取得手順を記載。
- 動作確認（localhost、実ブラウザ＝Playwright＋Chrome）：
  - **CDN 全遮断**（jsdelivr/googleapis を abort）でも顔検出が動作（「顔は自動で隠しました（1件）」）。読み込みは `/vendor/mediapipe/` の4ファイルのみ、CDN 要求 0 件。
  - **通し（判断不能の3棟）**：①選択3棟→②音声「1階にコンビニがあった」→質問「地上は何階建てですか」→「3階です」→確認「階数：3階 を入力します」→反映で階数3、③写真で顔1件自動ぼかし＋手塗り、④3棟とも usage/storeys を source=onsite で確定、⑤画面から CSV・GeoJSON ダウンロード（各棟が current_value=9999/461→proposed に、evidence_url に写真）、⑥画面からレポート生成（Markdown）。アプリ由来のコンソールエラー無し（favicon 404 のみ）。
  - 確認後、テストの3棟の確定・写真は削除してライブ DB を元に戻した（確定棟数 6→3）。
- 影響範囲：`web/vendor/mediapipe/`（新規）、`web/survey.js`（読み込み元）、`.gitignore`・`Makefile`（WASM 取得）、`docs/attribution.md`。既存機能は不変。

## 2026-11-06 現地写真：顔の自動ぼかし（MediaPipe）＋手塗り（黒塗り）を実装

- 背景 / きっかけ：前日の調査どおり、顔は MediaPipe で自動ぼかし、プレート等は手塗りで対応する方針で実装。画像は端末内処理・サーバーに送らない。
- 実装（`web/survey.js` / `survey.html`）：
  - **顔の自動ぼかし**：撮影（ファイル選択）→端末内で縮小→**MediaPipe Face Detector（`@mediapipe/tasks-vision`、Apache-2.0、BlazeFace short-range）** で顔検出→各顔をモザイク化（縮小→拡大。canvas filter 非対応でも効く）。モデル/WASM は jsdelivr＋googleapis から**初回だけ**読み込み（`import()` を1度だけ、`faceDetectorPromise` で保持）。**検出失敗・非対応は `-1` を返して手塗りへ進む（止まらない）**。
  - **手塗り（確認画面）**：自動ぼかし後に全画面エディタを出し、指でなぞって**黒く塗りつぶし**（ぼかしでなく塗りつぶし＝復元不可）。太さ3段階、やり直す（直前のストローク取り消し）。土台＝自動ぼかし済み画像＋ストローク配列を都度再描画。メッセージ「顔は自動で隠しました（N件）。ナンバープレート・表札・人が…必要なら指で塗ってください」（非対応時は「この端末では顔の自動ぼかしが使えません…」）。
  - **保存**：「この画像を使う」で加工後の canvas を `toDataURL` し、それだけを `currentPhoto` に。**元画像は端末にも残さない**（`currentPhoto` は加工後のみ、土台 canvas は閉じるとき破棄）。
  - **顔件数の記録**：`photo_faces` を `/api/survey` へ送り、`_save_survey_photo` が evidence(kind='photo') の detail に `faces_blurred` として残す（`api/main.py`）。非対応時は null。
  - 制約どおり：**画像はサーバーに送らず端末内で処理**／非対応端末は自動ぼかしを飛ばして手塗りのみ／`docs/attribution.md` に MediaPipe（Apache-2.0）を追記（`/attribution` 画面にも反映）。
- 動作確認（localhost、実ブラウザ＝Playwright＋Chrome。PD顔画像で検証）：
  - **対応端末**：顔1件を検出・ぼかし→「顔は自動で隠しました（1件）」。手塗りで canvas が変化、やり直すで土台に復元。「この画像を使う」で下書きに**加工後画像＋`photo_faces=1`**。編集〜確定の間、**自画像のサーバー送信は 0 件**（モデルは CDN から取得）。アプリ由来のコンソールエラー無し（favicon 404 と MediaPipe の TFLite INFO ログのみ）。
  - **非対応端末（CDN遮断）**：「この端末では顔の自動ぼかしが使えません…」→手塗りのみで画像生成、`photo_faces=null`。
  - **サーバー保存**：写真のみ POST→ evidence.detail に `faces_blurred: 2` を記録（確認後テスト痕跡は削除）。
- 影響範囲：現地写真の撮影経路（`web/survey.*`）と `api/main.py`（`SurveyBody.photo_faces`・`_save_survey_photo`）。日本のナンバープレート自動検出は前日調査どおり見送り（手塗りで対応）。

## 2026-11-06 調査：ブラウザ内の顔・ナンバープレート検出OSS（実装はまだしない）

- 背景 / きっかけ：現地写真や Mapillary 画像の顔・ナンバープレートを、**サーバーに送らず端末内で**ぼかしたい。使えるOSSを調べ、半日で組み込めるか判断する（実装はしない）。CLAUDE.md の Mapillary 規約＝「ぼかし解除・再識別はしない」。ここで作るのは逆向き（ぼかしを足す）なので整合。
- 候補と調査結果：
  - **顔検出（＝実用的な選択肢あり）**
    - **MediaPipe Face Detector（`@mediapipe/tasks-vision`）**：ライセンス **Apache-2.0**。BlazeFace 短距離モデル **約230KB（float16）**、モバイルGPU/WASM 向けに設計、ブラウザ内で完結（アップロード不要）。ランタイム WASM は別途数MB（初回のみ）。**本命**。
    - **@vladmandic/face-api（face-api.js の保守フォーク）**：**MIT**、保守継続。Tiny 検出器 **約190KB**、SSD MobileNet は約5.4MB。TF.js 依存。元の face-api.js は MIT だが更新が停滞。
    - **TF.js BlazeFace**：**Apache-2.0**。顔検出のみ。MediaPipe とほぼ同系。
    - **OpenCV.js Haar Cascade**：OpenCV は **Apache-2.0**。ただし WASM 本体が **約6MB** と重く、顔Haarは BlazeFace より精度・速度で劣る。スマホには不利。
  - **ナンバープレート検出（＝使える日本向けOSSが無い）**
    - ブラウザで動く検出器は **Ultralytics YOLO 系が大半＝ライセンス AGPL-3.0**（例：YOLO11 ベースの各種）。AGPL は強いコピーレフトで本プロジェクトの「Apache-2.0 / MIT なら可」に反する。
    - **OpenIPC/lpr-wasm**：コード MIT だが **重みが CC BY-NC 4.0（非商用）**。自治体利用を想定すると非商用条件が不適。約9MB。学習も日本プレート前提ではない。
    - **PlateYOLO-JP-Prototype（日本プレート）**：**データセットが非公開のプロトタイプ**（作者の自宅周辺で収集）。再利用できる学習済みモデル・ライセンスが揃っておらず、実運用に使えない。
    - OpenCV の Haar は `haarcascade_russian_plate_number.xml` はあるが **日本プレート用カスケードは無い**。日本のプレートは寸法・様式が異なり、海外前提モデルの転用は検出漏れが多い想定。
- 判断：
  - **顔：半日で組み込める見込み。** MediaPipe Face Detector（Apache-2.0・約230KB・端末内）で検出→ぼかし矩形を canvas に描いて保存画像に反映。現状の写真は `survey.js` で端末内縮小済みなので、その後段に差すだけ。
  - **日本のナンバープレート：自動検出は現状不可。** ライセンス清潔（Apache/MIT）かつ日本プレート対応の、ブラウザで動くOSSモデルは見当たらない（選択肢は AGPL / 非商用 / 非公開データのいずれか）。→ **自動化は半日では無理**。
  - **代替（検出漏れ対策と兼用）**：**人が手で塗る（ドラッグで矩形ぼかし）UI** を用意し、プレートはこれで対応。顔も自動検出の取りこぼしをこれで補う。手塗りUIは概ね半日規模。
  - まとめ：**顔の自動ぼかし（半日）＋ 手塗り（半日）** が現実的な構成。日本プレートの自動検出は、ライセンス清潔なモデルが現れるまで保留。ライセンス整合は Apache-2.0/MIT とも ODbL・CC BY-SA のデータ/画像と併用可（コピーレフト・非商用の AGPL/CC BY-NC は不可）。
- 影響範囲：調査のみ。コードは変更していない。実装する場合の着手先は `web/survey.js`（写真処理の後段）と `web/survey.html`（既存の注意書き `#photo-note` の近く）。

## 2026-11-06 音声入力：後勝ち・反映前の確認・メモ整理を追加

- 背景 / きっかけ：振り分け結果が自動で各欄に入るため、言い直しや誤認識がそのまま入ってしまう。確認を挟み、言い直しは後勝ちにする。
- 判断（3点）：
  1. **後勝ち**：同じ項目を複数回言ったら最後を採用。発話をまたぐ場合はフロントの上書きで（「5階建て」→反映→「3階建て」→反映 で 3）、1発話内の食い違い（「5階、いや3階」）は Claude 側で後の値を採るよう `VOICE_SYSTEM` に明記。
  2. **反映前に確認**：振り分け後に自動で各欄へ入れず、「用途：X ／ 階数：Y を入力します」と確認パネルを出し、**「反映」を押したときだけ**各欄へ入れる。「やり直す」はこの発話を取り消してメモを戻す（欄は不変）。
  3. **メモ整理**：反映時に、メモを「発話前メモ ＋ 反映しなかった特徴（`features`）」に作り直す。用途・階数として反映済みの文言は消え、特徴などは残る。何を反映し何を残したかを音声ステータスに表示。
- 実装：`web/survey.js`（`vProposal` 状態、`showProposal`/`applyProposal`/`redoProposal`、`finishVoice` を「確認を出す」方式に変更、棟移動・言い直しでリセット）、`web/survey.html`（確認パネル `#voice-proposal`）、`api/voice.py`（1発話内の後勝ちをプロンプトに追加、キャッシュ v4）。`dont_know`（判断不能）は欄を変えないので確認不要で即時記録。
- 動作確認（localhost、実ブラウザ＝Playwright＋Chrome。音声認識だけ疑似実装に差し替え。棟 13104-bldg-2923）：
  - 「5階建て、角地にあります」→ 確認パネル「階数：5階 を入力します」。**反映前は階数欄が初期値39のまま**。反映後に 5。メモは「角地に立地」だけ残る（5階の文言は消える）。
  - 続けて「3階建てです」→「階数：3階 を入力します」→ 反映で **最終 3（後勝ち）**。
  - コンソールエラーはアプリ由来 0 件（`/favicon.ico` の 404 のみ）。1発話内「5階、いや3階」→ `storeys=3` もサーバー単体で確認。
- 影響範囲：音声入力のみ。手入力・`/api/survey`・非対応端末は不変。

## 2026-11-05 音声入力に聞き返しを追加：足りない項目を Claude が1つずつ尋ねる（1往復1質問・最大3）

- 背景 / きっかけ：音声で一度に全部話すとは限らない。埋まらない項目を、その建物の状況に合わせて聞き返せるようにする。
- 判断（同じ API 呼び出しで振り分け＋質問）：`/api/voice_parse` を拡張し、認識テキストの振り分けに加えて**質問を1つ**返す。
  - リクエストに現在の状態（`status`＝用途/階数の filled・値・推定状態）、直前の質問項目 `pending`、既に「分からない」と答えた `givenup`、往復数 `asked` を受ける。
  - Claude は構造化出力で `usage/storeys/features` に加え `dont_know`（分からないと答えた項目）・`question`・`question_target` を返す。
  - **質問はサーバーが最終決定**：埋まっていない項目を「**判断不能 > 低信頼 > その他**」の順に並べ、最優先の1つだけ尋ねる（Claude の文面はその項目に一致したときだけ採用、外れたら定型質問に落とす）。全部埋まっている/確認済み/分からない済みなら `question=null`。
- 制約（要件どおり）：**質問は1度に1つ**。`asked>=3`（3往復）で打ち切り。「分からない」と答えた項目は `dont_know`→**判断不能として記録し、もう聞かない**（UI は「判断不能」チップ、サーバーは往復数・最終的に埋まった項目・判断不能にした項目をログ `voice_parse_log.jsonl` に残す＝デモ材料）。
- 画面（`web/survey.*`）：メモ欄の上に**質問を大きく1つ**表示し、同じ🎤ボタンでも手入力でも答えられる。答えるとまた振り分けて次の質問を出す。用途/階数の**埋まり具合と往復数**を小さく表示。質問は状態に依存するためキャッシュ键に状態を含める（cache v3）。
- 動作確認（localhost、ハンドラ直呼び。※常駐サーバーは旧コードのため再起動が必要）：
  - 階数が判断不能の棟で「1階にコンビニがあった」→「**地上は何階建てですか**」（target=storeys）を質問。
  - その質問に「分からない」→ `dont_know=["storeys"]`、以降 `question=null`（もう聞かない）。
  - 用途・階数とも確定済みの棟 → `question=null`（質問なし）。
  - `asked=3` → `question=null`（打ち切り）。ログに `asked/filled/gave_up` が残ることを確認。
- 影響範囲：音声入力のみ。非対応端末・音声不使用・`/api/survey` は不変。

## 2026-11-05 現地調査に音声入力（試験的）。要件「端末内処理」は満たせないため明示＋同意に変更

- 背景 / きっかけ：現地調査の1棟入力を、手を使わず声でも行えるようにしたい（試験的・デモで使えるか動かして判断）。
- 事前調査（Web Speech API / SpeechRecognition、実装前に確認）：
  - iOS Safari は 14.5 以降 `webkitSpeechRecognition` 対応。ただし**連続認識が不安定**（途中で切れる／マイクが止まらない報告）。Android Chrome は実用的。**Firefox は非対応**。日本語 `ja-JP` は可。
  - マイク権限が必要。**secure context 必須**＝localhost 以外は HTTPS が要る。
  - iOS の連続認識は `onend` で自動再開して凌ぐ（完全ではない）。
- **前提の崩れ（止めて相談した点）**：要件「音声データは端末内で処理し、保存しない」のうち**「端末内で処理」は Web Speech API では実現できない**。認識はブラウザ提供元（**Chrome→Google / Safari→Apple**）の**サーバー**で行われる。
- 判断（相談のうえ要件を修正）：
  1. **明示＋同意**：音声を使う前に一度だけ「音声はブラウザの提供元（Google/Apple）のサーバーで文字に変換される。まちカルテは音声を保存せず、文字だけを扱う」と表示し、了解した人だけが使う（同意は localStorage）。
  2. **録音しない**：音声データは保存せず、認識後テキストだけを扱う（当初方針どおり）。
  3. **代替を残す**：音声が非対応・拒否・失敗でも、**手入力がそのまま使え、入力は壊れない**。対応端末でだけボタンを出す。
- 実装：`api/voice.py` ＋ `POST /api/voice_parse`。認識後テキストのみ受け取り、Claude の構造化出力（`output_config` の json_schema、`claude_infer` と同方式）で用途コード・地上階数・特徴に振り分ける。**読み取れない項目は null（推測で埋めない）**。用途は用途コード一覧の enum に限定し、一覧外は null に落とす。入力ハッシュでキャッシュし、トークン・費用を `data/derived/voice_parse_log.jsonl` に記録。UI は `web/survey.html` / `survey.js`（メモ欄に「🎤 音声で入力」、同意モーダル、認識テキストはメモに追記して手入力を消さない）。
- 動作確認（`CLAUDE_MODEL` 未設定→既定 `claude-opus-5-5`）：
  - 「3階建ての事務所ビル、1階にコンビニ、看板が新しい」→ `storeys=3`／`usage=404`／`features` に特徴。2回目は `cached=True`。
  - 「よくわからない建物」→ usage/storeys/features すべて null（推測しない）を確認。
  - 空テキストは API を呼ばず null を返す。`tool_choice:{type:"tool"}` は現行モデル非対応だったため `output_config` 方式に合わせた。
- 影響範囲：現地調査モードのみ。`/api/survey`（確定の書き込み）は不変。非対応端末・音声不使用の経路に影響なし。HTTPS 必須のためスマホ実機確認はトンネル（cloudflared / ngrok）が要る（手順は `web/README.md`）。

## 2026-11-05 現地写真を建物の画像として使う：撮影日・撮影者つきで確認画面/地図/レポート/CSV に

- 背景 / きっかけ：現地調査（source=onsite）で撮った写真は `data/survey/` に保存して `evidence(kind='photo')` に紐づくだけだった。これを「その建物の画像」として、確認画面・地図・レポート・CSV で使えるようにする。
- 判断：
  - **撮影日は `evidence.created_at`（サーバーの時計）を正本**、**撮影者は `evidence.detail.operator` に保存**（写真のみ登録＝confirmations が無い場合でも撮影者を辿れるように）。`_save_survey_photo` に operator を渡すよう変更。
  - **API**：`/api/buildings/{id}` に `photos`（url・captured_on・operator・source、撮影順）を追加。地図マーカー用に **`GET /api/photos`**（写真のある棟の重心・枚数・最新撮影日）を新設。
  - **確認画面（`web/app.js`）**：画像枠に現地写真を出す。複数なら ‹ › で切り替え。供給源ラベルは「現地調査（YYYY-MM-DD 撮影）・撮影者」。写真が無ければ従来の LOD2/画像なし文言にフォールバック。
  - **地図**：写真のある棟をシアンの点（`b-photo` 円レイヤ）で示し、クリックでその棟を開く。
  - **レポート（`export/report.py`）**：現地調査の節に代表写真を最新 1〜2 枚、**相対パス `../../survey/<file>`**（`data/derived/export` から見た `data/survey`）で Markdown 埋め込み。撮影日・撮影者をキャプションに。Claude プロンプトからは写真ファイル名を除外。
  - **CSV（`export/update_candidates.py`）**：`evidence_url` に写真の参照（`/survey/...`）を追加。**写真本体は同梱しない（容量・権利）＝参照のみ**。
  - **注意喚起（`web/survey.html`・`survey.js`）**：写真欄に常時「⚠ 人や車のナンバー・表札が写っていないか確認してください」。撮影後は赤で強調。
  - 写真は `data/survey/` のまま（git 管理外を維持）。
- 根拠（実データのコピーで非破壊検証。再現：DB をコピーし onsite 確定＋写真2枚を投入）：`/api/photos` SQL＝1棟2枚・重心(139.68735,35.69505)・最新 2026-11-06。`_photos_for`＝captured_on 2026-11-06・撮影者「山田 太郎」。`fetch_rows` の `evidence_url` に `/survey/..._1.jpg;/survey/..._2.jpg`。`compute_facts.field_survey.sample_photos`＝2件、`render_markdown` が `![...](../../survey/...)` で2枚埋め込み＋「同梱しない」注記。`node --check`（app.js/survey.js）OK、`py_compile` OK、`pytest tests/test_export.py tests/test_report.py` 7 passed。**稼働中サーバーは `--reload` 無しで起動のため新コード未反映＝再起動が必要**（検証は DB コピーで実施、ライブ DB は未変更）。
- 影響範囲：`api/main.py`（`_save_survey_photo` に operator・`_photos_for`・`/api/buildings` の photos・`/api/photos`）、`web/app.js`・`style.css`（画像枠の写真表示＋切替、`b-photo` レイヤ）、`web/survey.html`・`survey.js`（注意喚起）、`export/update_candidates.py`（`evidence_url` に写真参照）、`export/report.py`（代表写真の埋め込み）。写真は `data/survey/` のまま git 管理外。

---

## 2026-11-05 画像なしの外形比較（OSM / Overture）でのずれ検出：調査のみ・絞り込みには不採用

- 背景 / きっかけ：画像を使わず、PLATEAU の外形と外部ベクタ（OSM 建物ポリゴン・Overture Buildings）のずれ（IoU・属性の食い違い）で「調査すべき棟」を絞り込めるかを調べる（実装はしない）。画像判定を後回しにした（2026-11-03）後の、別ルートの見極め。
- 判断：**使えない。止める。** 外部ベクタの外形比較は**調査トリガにしない**。変化検出は既存の `estimate/version_diff.py`（PLATEAU 2023↔2025、同一出自の ID＋IoU、中央値 1.000・年区間つき）を主とする。理由は下の5点：
  1. **外部2源は独立でない。** Overture Buildings はデモ範囲で **96.5% が OpenStreetMap 由来**（下記）。「OSM と Overture の2源で裏取り」は成立せず、実質 OSM 1源。
  2. **ずれの大半は構造ノイズ（分割粒度の違い）。** OSM/Overture は PLATEAU の約2倍の別棟に分割（長屋・附属屋・建物部分）。縁効果を全 PLATEAU 照合で補正しても Overture-only は **58.6%** 残る＝アーティファクトでなく本質的。本当の変化と機械的に分離できない（＝設計書「ノイズと本当の変化を区別」で区別不可側）。
  3. **建築年が無い。** OSM にも Overture にも建築年がなく、Overture の `update_time` は **OSM の編集時刻**（2011–2026 に散在、2012/2020 に取込スパイク）であって竣工年ではない。よって「PLATEAU 2025年度版より新しいか」を判定できない＝変化検出の肝が欠ける。
  4. **対応が取れる棟は属性もよく一致**（新情報が薄い）：高さ中央差 0.0m、階数は OSM `building:levels`/Overture `num_floors` と PLATEAU(h÷3.8) が |diff|≤1 で 71%。食い違う棟も真の変化とタグ誤りを切り分けられない。
  5. **PLATEAU-only（外部に無い）は不明群で 43–49% と高いが、OSM の過少マッピングが主因**で「新築・変化」の証拠にならない（OSM 被覆との交絡）。
  - **唯一の限定的な余地**：OSM `building:levels` / Overture `num_floors` を、外部対応のある不明群（331–367棟）の**階数推定の evidence チップ**として添える程度（ターゲティング信号ではない）。やるなら candidates ではなく evidence に入れる（公式データ非上書きの原則どおり）。
- 根拠（実データ・デモ範囲で実測。再現方法つき）：
  - **範囲**：都庁中心・約2km角 bbox（W139.6807 S35.6806 E139.7027 N35.6986、合意済みデモ範囲）。PLATEAU 外形（`data/derived/buildings.geojson`、重心が bbox 内）＝**4,559 棟（不明群 t=1：645）**。
  - **OSM**（Overpass API, 2026-11-05 取得, UA 必須＝無いと 406, ODbL）：建物 way ポリゴン **9,447**＋relation 184。
    - PLATEAU↔OSM：重なりあり **88.6%** / PLATEAU-only **11.4%（521）**。**median IoU 0.805**。IoU<0.3＝475（10.4%）。複数 OSM と重なる（分割差）**13.2%**。
    - 不明群 645：OSM 対応 367 / PLATEAU-only **278（43%）**、うち低 IoU(<0.3) 173。
    - 高さ：matched(IoU≥0.3) で両方に値 n=1,721、中央差(PLATEAU−OSM) **0.0m**、|差|>3m は 156（9%）。`building:levels` あり 1,876。
    - OSM-only 60.4%（縁効果を含む粗値）。
  - **Overture Buildings**（`s3://overturemaps-us-west-2/release/2026-10-21.0`、スキーマ v2.0.0、CC BY 4.0＋sources 継承、DuckDB httpfs で bbox プッシュダウン取得 ≈51s）：bbox 内 **9,109 棟**。
    - **sources：OpenStreetMap 8,789（96.5%）/ ML(Zenodo doi:10.5281/zenodo.8174931) 197 / Microsoft ML 123** → 密集部では実質 OSM。
    - PLATEAU↔Overture：重なり **87.9%** / PLATEAU-only **12.1%**。**median IoU 0.743**。不明群 PLATEAU-only **49%（314/645）**。分割差 15.4%。**Overture-only 58.6%**（全 PLATEAU 57,780 と照合＝縁効果補正済）。
    - `num_floors` クロス（IoU≥0.5, n=705）：PLATEAU(h÷3.8) と |diff|≤1 が **71%**、median diff 0。属性保有は height 1,464 / num_floors 1,801（9,109 中）＝疎。
  - **再現**：Overpass（`way/relation["building"]`＋UA）→ DuckDB spatial（in-memory, ファイルロック回避）で IoU。Overture は上記 S3 を bbox で取得。IoU は `ST_Transform`(PROJ) 不在のため経緯度のまま算出（約2km角では局所アフィン≒一定で面積比＝IoU は保存）。スクリプトは scratchpad の投げ捨て（**リポジトリに入れない**＝実装しない方針どおり）。取得した OSM/Overture データも git 管理外。
  - **作業見積（やる場合の目安）**：新宿区全域の OSM/Overture 取得＋relation/マルチポリゴン組立＋EPSG:6677 再投影＋分割対応（1:N・N:M）IoU＋ノイズ裾の人手トリアージで概ね **2–3日**。ノイズ主体かつ建築年の無い信号に見合わず、半日ルートのオルソ視認レイヤ（2026-11-05 の別エントリ）や既成の version_diff に劣る。
- 影響範囲：調査のみ。コード・データはリポジトリ未追加。画像なしの変化検出は **version_diff を主**とし、外部ベクタの外形比較は不採用（将来、階数推定の evidence として `num_floors`/`building:levels` を限定利用する余地のみ残す）。

## 2026-11-05 訪問順の提案（距離・近似TSP）。経路の線は引かない（規約順守）

- 背景 / きっかけ：「距離から順番を提案し、最適なルートを表示したい」という要望。ただし "ルート表示（経路を描く）" は Mapillary 規約（ナビ・経路案内の禁止＝CLAUDE.md「絶対に守ること」）と、前回自分で決めた「経路は引かない」方針に抵触するため、止めて相談した。
- 判断（ユーザー合意）：**順番の提案だけ実装し、線は引かない**（点と番号のみ）。
  - 起点は **現在地（「近い順」を押して取得できたとき）／なければ優先度1位の棟**。
  - 近似TSP（**最近傍 → 2-opt**）で訪問順を決める。経路案内ではなく「回る順番」の提示に用いる。
  - 既存の **200mグループと併用**：グループの訪問順（重心で NN+2-opt）も、グループ内の順番（入口＝直前の出口）も最適化する。
  - 各棟に通し番号を振り、俯瞰地図のピンにも番号を出す。
  - **総移動距離**を「この順で回ると 約N km」と出す。**直線距離の合計である旨を明記**（道路距離ではない）。
  - 地図に線（補助線・ルーティング）は引かない。
- 根拠（実データ・配信中サーバーで確認）：実座標12棟で NN=3,086m→2-opt=2,791m（短縮・順列保持）を Node で確認。`node --check` OK、`/survey.js`・`/survey.html` 200 で新コード配信、参照 id は全て HTML に存在。選択→プラン→入力の各画面が依存する API（stats/usage_codes/unresolved/targets/buildings/survey）はいずれも 200、入力送信（POST /api/survey 写真なし）も onsite 記録を確認（検証用の投入は後で削除）。
- 影響範囲：現地プランが「距離最適化した訪問順＋番号＋総距離（直線）」になった。線・ルーティングは規約により不採用。位置情報は従来どおり表示と並び替えのみ（送らない・保存しない）。

## 2026-11-05 現地調査モードを作り直し：地図で選び、現地は近い順にグループで回る

- 背景 / きっかけ：現地調査モードをリスト選択から作り直す。地図で選び、現地では近い順に回れるようにする。位置情報は表示と並び替えにだけ使い、サーバーに送らない・保存しない。位置が取れなくても全機能が使えること。
- 判断（`web/survey.html`・`web/survey.js` を全面刷新。3画面構成）：
  - **選択（地図が主役）**：調査対象を点で色づけ。初期は優先度上位100棟、「もっと表示」で 500→2000→全4,982 に増やす。タップで選択/解除（色が変わる）、タップで建物ID・高さ・推定用途・判断不能ガイドを小さく表示。**指ドラッグの矩形で範囲選択**（ドラッグ中はパンを止め、矩形内の表示中の棟を選ぶ）。下部に「選択中N棟」＋折りたたみ一覧（個別に外す）。地図が重い端末向けに**リスト選択も併設**。
  - **調査プラン**：「優先度順」（既定）/「近い順」の切替。**近い順を押したときだけ** `navigator.geolocation` を要求し、取れなければ「位置を取得できませんでした」と出して優先度順のまま。選んだ棟を**半径200mでグループ化**（アンカー＝先頭、グループ内は最近傍の鎖順）、各棟に現在地からの距離（「約180m」、位置が取れたときだけ）。**番号付きピンの俯瞰地図**を1枚、現在地の点は取れたときだけ。**経路は引かない**（ナビに見えない／Mapillary 規約）。グループ見出しは番号（駅名・地名の自動付与は未実装＝CORS/オフラインを避け「無ければ番号」に倒した）。
  - **入力**：既存の1棟入力をそのまま流用し、グループ番号と距離の行だけ追加。
  - 選択は MapLibre の feature-state ではなく **paint 式（`in` ＋選択ID配列）**で塗る（建物IDが数値化できず feature-state が使えないため）。
  - 位置情報は in-memory のみ（localStorage にも POST にも出さない）。入力の下書きは従来どおり localStorage。
- 根拠（実データ・配信中のサーバーで確認）：`/api/targets?limit=5000`＝4,982件（座標つき、点なので軽い。28MBのポリゴンGeoJSONは使わない）。`node --check` 構文OK、`/survey.html`・`/survey.js` 200で新コード配信、JS が参照する41個の id が HTML に全て存在。375px 前提で max-width 480・100dvh・タップ半径8〜11pxで設計。
- 影響範囲：現地調査モードの選択体験が地図中心になり、現地での回り方（近い順・200mグループ・俯瞰）が加わった。位置情報はオプトイン（近い順を押したときだけ）。入力・送信・source=onsite の経路は不変。

## 2026-11-05 デモの仕上げ：低確度は確定前に一拍、確定者名を既定「調査担当者」＋変更可

- 背景 / きっかけ：高さ119.5mの棟に「供給処理施設」（用途・階数とも信頼度「低」）が、そのまま確定できてしまう。誤確定を防ぎ、現地確認へ逃がす導線を作る。確定者名の既定も整える。
- 判断：
  - **「そのまま確定」に低確度ゲートを挟む**（`web/app.js` の `attemptKeep`）。確定しようとする属性（usage/storeys/year_built）に `confidence_label==='低'` があれば、`#lowconf-modal` を出す：「この属性は確度が低いため、現地確認を推奨します。それでも確定しますか？」。選択肢は3つ — (1) **現地確認リストに追加（確定しない）**＝`localStorage: mk.survey.selected` に積み、現地調査モード（/survey.html）がそのまま読む、(2) それでも確定する、(3) やめる。Enter も同じゲートを通す。「修正して確定」はゲート対象外（人が値を入れているため）。
  - **確定者名の既定を「調査担当者」に**。確認カードに確定者の入力欄を追加し、`localStorage: mk.desk.operator` に保存して毎回の確定に送る。出力側の未入力フォールバックも `担当者`→`調査担当者` に統一（`export/update_candidates.py`、`tests/test_export.py` の既定値も更新）。
  - 現地確認に回した棟は優先度リストに「現地予定」タグを出す（紫）。確定はしないので候補リストには残る。
- 根拠（実データで確認、サーバー起動中）：`/api/buildings/13104-bldg-1177`（119.5m）は usage・storeys とも `confidence_label='低'` → ゲートが発火する条件を満たす。配信中の app.js/index.html/style.css が新コード（`attemptKeep`・`#lowconf-modal`・`.lc-box`）であることを確認。`pytest` 31 passed。
- 影響範囲：机上の確定フローに「低確度の一拍」が入り、低確度は現地調査モードへ受け渡せるようになった（2つのモードが localStorage の選択リストでつながる）。確定者名はデモで実在の担当者名に差し替え可能。

## 2026-11-05 デモを画面だけで完結：出力はAPI経由、ロック時はCLIが親切に止まる、確定経路をCSVに

- 背景 / きっかけ：通しで見つかった3点（demo-reset 不在／DuckDB ロックで起動中に出力不可／CSVで現地と机上が混ざる）を直し、デモをターミナルに切り替えず画面だけで回せるようにする。
- 判断：
  - **`make demo-reset` を追加**（`reset-confirmations` の別名、両方残す）。あわせて優先度リストを**西新宿（都庁1km圏）先頭・score降順・building_id で決定**に変更（`api/priority.py` の `NISHI_FLAG_SQL` と ORDER BY）。初期表示も都庁なので、画面の位置と優先度トップが毎回一致し、同じ棟から始められる。
  - **DuckDB は単一プロセス書き込み**。出力を画面から行えるよう、CSV/GeoJSON は既存の API（`/api/export.csv`・`/api/export.geojson`、共有 RW 接続）で取得、**レポートは `POST /api/report` を新設**（`compute_facts` は `db_lock` 内、時間のかかる Claude 生成 `build_markdown` はロック外）。`export/report.py` を接続非依存の `build_markdown(facts,...)` に分離して CLI と API で共用。`web/` にレポート生成ボタンを追加。
  - **CLI は残すが、サーバー起動中に叩いたら親切に止める**。`duckdb.IOException`（lock）を捕まえ、トレースバックではなく「サーバーを止めてください／画面の機能を使ってください」と出して `exit 1`（`export/__main__.py`・`export/report.py`）。
  - **確定経路を CSV/GeoJSON に出す**。`source`（推定の出どころ）とは別に **`confirmed_via`（desk / onsite）** 列を追加（`confirmations.source` 由来、`export/update_candidates.py`）。`docs/design.md` の列定義と同梱 README にも追記。
  - サーバーは `.env` を読む（`api/main.py` で `load_dotenv()`）＝レポートの Claude 生成に API キーが要るため。
- 根拠（実データで確認、サーバー起動中）：優先度トップ6が全て西新宿（都庁 ≤1km, 713〜902m）・2回取得で並び一致。`make export`/report CLI はロック時に親切メッセージ＋exit。画面経路で 机上2＋現地1 を確定→`/api/export.csv` に `confirmed_via` 列（現地棟＝onsite / 机上＝desk）→`POST /api/report` 200・現地節1棟。`pytest` 31 passed。demo-reset 後はクリーン（0/0）・トップ=1177(119.5m)。
- 影響範囲：デモはすべて画面から（確定→ダウンロード→レポート生成）。CLI は開発者向けにサーバー停止時のみ。優先度の並びは西新宿先頭に変えた（score の意味は各グループ内で不変）。

## 2026-11-05 通し確認：確定0件のレポートはClaudeを呼ばない／DuckDBはサーバーとCLIが排他

- 背景 / きっかけ：ingest から通しで動かした直後（confirmations が空）に export と report を叩いたらどうなるかを確認し、通しの動作確認（reset→確認3棟→現地1棟→export→report）を実施した。
- 判断：
  - **export は確定0件でも落ちない**（ヘッダのみの CSV＝BOM 付き、GeoJSON features 0）。修正不要と確認。
  - **report は確定0件のとき Claude を呼ばない。** これまでは0件でも1回 API を叩いていた（約$0.024）。数字の根拠が無いので `confirmed_buildings==0` で生成をスキップし、『確定された棟がありません』を明示する専用出力（`_empty_report`）を返す（API 0回）。確定に依存しない「現地確認が必要な棟」の実数だけは参考に載せる。`tests/test_report.py` で固定。
  - **DuckDB は単一プロセス書き込み。** API サーバー（read-write 接続を常時保持）が起動している間は、`make export` / `uv run python -m export.report` / `eval` 系テストが同じ DB を開けず `IOException: Could not set lock` で落ちる。**手順：CLI の export/report はサーバーを止めてから実行し、必要なら再起動する**（起動中にダウンロードだけ欲しいときは API の `/api/export.csv` `/api/export.geojson` を使う）。`make test` もサーバーを止めてから。
  - Makefile に `demo-reset` は無い。確定履歴のリセットは **`make reset-confirmations`**（`api.schema --reset-confirmations`）。
- 根拠（実データで確認）：reset 後 export=0棟/0行・GeoJSON features 0、report=API 0回で『確定された棟がありません』。確認3棟（keep2/edit1）＋現地1棟（写真なし）を入れて、export は 4棟/8行（3 desk＋1 onsite）、report は 確認・確定の内訳（階数 4＝keep3/edit1、用途 4＝keep2/edit2）と「現地調査で確定した棟 1棟（写真0枚）」を出力。`/api/confirmed`＝desk3/onsite1、`/api/metrics`＝processed 3（desk 限定）。サーバー停止時 `pytest` 31 passed、起動中は eval_leak 3件が DB ロックで error（コード不具合ではない）。
- 影響範囲：report の空ガードで無駄な課金と無意味な文章生成を止めた。運用は「サーバーと CLI の DB アクセスは排他」を前提に手順化する。CSV では onsite の値も `source=manual` として出る（現地の区別は DB の `source` 列・レポートの現地節・`/api/confirmed` で持つ）点は仕様として残す。

## 2026-11-05 現地調査モード（スマホ）：source で desk/onsite を分離、位置情報は使わない

- 背景 / きっかけ：机上で絞り込んだ不明群を、担当者がスマホのブラウザで現地入力し、確定・地図・レポートに反映する。
- 判断：
  - **`confirmations` に `source` 列を足し、机上='desk' / 現地='onsite' を区別する。** 公式データは上書きせず、現地の確定も既存同様 `confirmations` に残す。既存 DB には `ALTER TABLE ... ADD COLUMN IF NOT EXISTS source VARCHAR DEFAULT 'desk'`（`api/schema.py` の `_MIGRATIONS`）で冪等に追加。既存行は 'desk'。
  - **受け口 `POST /api/survey`（`api/main.py`）**：usage/storeys/写真/メモを1棟ぶん受ける。写真は data URL を base64 デコードして `data/survey/`（git 管理外）に保存し `evidence(kind='photo')` に紐づける（Claude 多モーダルの `kind='image'` と分ける＝再推定で消えない）。usage/storeys は与えられた分だけ `source='onsite'` で確定。`accepted_candidate` は推定値一致で TRUE。**写真なしでも受理**、入力皆無なら 400。
  - **計測 `/api/metrics` は `source='desk'` に限定。** 現地は opened_at≒confirmed_at で0秒寄りになり「1棟あたり処理時間」の中央値を歪めるため。デモの数字の意味（机上トリアージ速度）を保つ。
  - **地図反映**：`GET /api/confirmed`→`{onsite,desk}`、`web/app.js` に緑レイヤ `b-done-onsite` を追加して現地確定棟を塗る。
  - **フロント（新規 `web/survey.html` + `web/survey.js`）**：スマホ縦1画面（max-width 480px, 100dvh）。棟選択⇄1棟調査。位置の代わりに地理院タイルのミニ地図（**Geolocation は使わない**＝許可ダイアログで現地が止まるのを避ける）。入力は都度 localStorage に下書き保存（通信断でも消えない）。写真は canvas で長辺1280pxに縮小して data URL 化（localStorage と送信を軽く）。
  - **レポート（`export/report.py`）**：`field_survey`（現地の棟数・件数・写真数・属性内訳）を SQL 集計で足し、「現地調査で確定した棟」節を描く（数字は Python、文章は Claude の分担は維持）。
- 根拠（実データで確認）：`pytest tests/` 29 passed。起動して `POST /api/survey` で usage+storeys 確定・写真保存・静的配信200を確認。`/api/confirmed` が onsite/desk を分離。`/api/metrics` は現地を除いて processed=9 のまま。写真なし送信=成功・入力皆無=400。レポートに現地節が出力。検証で入れた合成 onsite データと写真は掃除済み（desk 9棟は保持）。
- 影響範囲：確定の経路が机上と現地の2系統になった。`source` の分離で、計測・地図・レポートがそれぞれ正しく desk/onsite を扱う。375px の画面崩れは実機/エミュで各自確認する方針（Playwright は導入しない）。

## 2026-11-05 調査レポート生成（export/report.py）：数字は SQL、文章だけ Claude、API は1回

- 背景 / きっかけ：確定した棟を、自治体の担当者が上司に出せる報告書（Markdown）にする。CSV / GeoJSON と同じ `data/derived/export/` に `report_<city>.md` を置く。
- 判断：
  - **数字と文章の責任を分ける。** 数値はすべて SQL 集計（`export.update_candidates.fetch_rows` と `api.onsite.aggregate_unresolved` を再利用）で Python が確定し、本文の表も Python が描く。Claude には構造化出力で**文章欄（要約・所見・むすび）だけ**返させ、数字を作らせない。Claude が数字を取り違えても表の数字は正しいまま。
  - **API は1回の生成で1回だけ**呼ぶ（パース失敗でもリトライしない＝費用と約束のため。失敗時は素のテキスト／注記にフォールバック）。入力ハッシュでキャッシュし、トークン・費用を `data/derived/report_log.jsonl` に残す。モデルは `CLAUDE_MODEL`（`estimate.claude_infer` の `get_model`/`compute_cost` を共用）。
  - 既存テーブル・出力は触らない。`export.update_candidates` の `connect`/`fetch_rows`/`ATTRIBUTION` を再利用して SQL を二重に書かない。
- 根拠（実データで確認、`uv run python -m export.report`）：確定 9 棟 / 21 件（階数9・用途9・建築年3、いずれも「そのまま確定」）。用途内訳＝共同住宅3／住宅2／業務施設2／供給処理施設1／文教厚生施設1。建築年区間 2022〜2024。現地確認が必要＝不明群 4,982 棟中 521 棟（全て「高さ欠損で階数を推定不可」）。1回の生成で 1,567 tokens / $0.02896（opus-5-5）。再実行はキャッシュで課金なし。`pytest tests/` 29 passed。
- 影響範囲：第4段（出力）に報告書が加わった。数値表は Python、文章は Claude という分担は今後の文章生成（他区・他属性）でも踏襲する。

## 2026-11-05 衛星・オープンデータでのフットプリント比較：調査のみ（実装は見送り）

- 背景 / きっかけ：2時点（2023年以降 / 2025年以降）の衛星・オープンデータで建物外形の変化を見られるか調査した（実装はしない）。
- 判断：**2時点の画像比較による外形変化の自動検出は、新宿区では現時点で見送り。** 代わりに、使うなら東京都デジタルツインの 0.2m オルソ（2023）を**確認画面の視認レイヤ**として重ねる路線のみ現実的（概ね 0.5〜1 日）。新築・滅失の検出自体は既に `estimate/version_diff.py`（PLATEAU 2023 vs 2025 版の建物ID＋IoU）で成立済みのため、外部データの役割は一次検出ではなく人の裏取り。
- 根拠（Web 調査、2026-11 時点）：
  - 東京都デジタルツイン区部オルソ＝**0.20m GSD / GeoTIFF / CC BY 4.0**、撮影 **2023年2〜4月**。T1 として最適で継承義務なし（出典表示のみ）。
  - Sentinel-2＝10m。新宿の建物幅10〜20mで1〜4画素のため**輪郭比較には粗すぎる**（超解像は研究段階で製品ではない）。
  - 地理院 最新写真（シームレス）＝都市部 0.2〜0.4m だが撮影年がモザイクで混在し、新宿の「2025以降」が未確認。出典明示のみ。
  - Overture Buildings（ODbL）/ Microsoft Building Footprints（2026-03 に CDLA-Permissive-2.0 へ変更）＝ベクタで**撮影時点のタイムスタンプが無く**、2時点差分の素材にならない。
  - ライセンス：CC BY 4.0・出典明示は継承義務なしで既存の帰属枠に収まる。Mapillary の CC BY-SA と衝突する画像継承は発生しない。
  - **ブロッカー：** 2時点比較に必要な「新宿の ≥2025 のオープン高解像オルソ1枚」が未確認。追うならまず地理院シームレス写真の新宿エリアの実撮影年をタイルメタで確認する（0.5日）。
- 影響範囲：画像の役割は「新築検出の一次手段」ではなく「確定時の視認補助」に限定する方針を確認。実装は未着手のまま。

## 2026-11-04 判断不能の集約と「現地で何を見るか」画面：判断不能は unresolvable フラグで数える

- 背景 / きっかけ：用途は候補提示どまり、20m超の階数は候補扱い（上の2エントリ）。「自動では埋まらない棟」を担当者に渡し、現地で何を確認すればよいかを示す画面が要る。
- 判断：
  1. **判断不能の定義は `unresolvable=TRUE`（value=NULL だけで数えない）。** year_built は区間推定（value_min/max を使い value=NULL）で**判断不能ではない**ため、value=NULL で数えると version_diff の 5,805 件を誤計上する。実測、現地確認が必要なのは **storeys の高さ欠損 521棟のみ**（用途は近傍で常に候補値が付き判断不能ゼロ、Claude 推定は本番 candidates に未投入）。集約は**属性×理由で汎用化**し、将来 Claude 推定を candidates に入れれば用途の判断不能区分も自動で現れる。
  2. **集約は `api/`（eval 非依存）。** `api/onsite.py` の `aggregate_unresolved`（純SQL）。高さ帯は api 内に定義を持ち、西新宿1km圏は `priority.demo_counts` と同じ（都庁6677から ST_Distance≤1000）。区分ごとに帯別・エリア別の内訳と bbox・building_ids を返す。
  3. **Claude 要約は区分ごとに1回だけ**（棟ごとに呼ばない＝費用と時間）。入力ハッシュでキャッシュ＋費用ログ（CLAUDE.md）。**別ステップ `make onsite` で生成し `data/derived/onsite_guidance.json` に保存、API（`GET /api/unresolved`）は読むだけ**でリクエスト経路では Claude を呼ばない。system prompt で「現地確認でも埋まらない区分は別データが必要と正直に言う／埋め方をでっち上げない」を指示。
  4. **画面**：ヘッダに「4,982棟のうち、現地確認が必要な N棟」バー → パネルに区分（属性・棟数・理由・帯/エリアのチップ・Claude 要約）。区分クリックで**その区分の棟だけ地図に紫で絞り込み**（他を消す）＋ bbox へ寄せる。「解除」で戻す。要約未生成なら「make onsite で生成」と表示。
- 根拠（再現）：
  - `uv run python -m api.onsite`（集約のみ）/ `make onsite`（Claude 要約生成）/ `GET /api/unresolved`。
  - 集約：現地確認が必要 **521棟**（高さ無 521 / 西新宿1km圏 140・その他 381、bbox 139.68–139.74・35.67–35.73、building_ids 521）。
  - Claude 要約（claude-opus-5-5, API 1回 **$0.0074**）：「現地で外観から窓・外壁の段を数えて階数を記録、数えにくい高層は入口のフロア案内板やEVの階表示で最上階を確認、現地で難しければ区役所の建築計画概要書の階数で補う」＝**現地で埋まる区分**（正直に埋まらないと言う挙動も system prompt で担保）。
  - 生成物（`data/derived/onsite_guidance.json`・`data/cache/onsite_guidance/`）は git 管理外。クローン直後は要約 None（UI は「未生成」表示、集約は candidates から常に算出）。
- 影響範囲：`api/onsite.py`（新規）、`api/main.py`（`/api/unresolved`）、`Makefile`（`onsite`）、`web/`（index.html のバー＋パネル、style.css、app.js の `b-onsite` レイヤ＋絞り込み）。買い物リストではなく「現地で何を確認するか」を属性ごとに1つの文で渡せるようにした。

## 2026-11-04 階数精度を高さ帯別に測り直し：20m以上は構造的に低下（階高の付け方では直らない）→ 20m超を候補扱いに

- 背景 / きっかけ：直前の Claude 検証で階数が全体 30%/60% と低く見えたため、主軸である「高さ÷階高」の仮推定をホールドアウト全体で**高さ帯別**に測り直し、20m以上で落ちるかと、その原因（階高の当て方か、実際の階高のズレか）を切り分けた。
- 判断：
  1. **20m以上は階数を候補扱いにし、信頼度を下げる**（`storeys_confidence`：<20m=高(0.8) / 20〜45m=中(0.5) / 45m超=低(0.3)）。注記を45m超だけでなく **20m超へ拡張**（「階高のばらつきが大きく、階数は目安（±1階程度）」）。確認画面は `confidence_label`（高/中/低）と reasoning をそのまま描画するため、**UI コード変更なし**で反映される。`make estimate` で入れ直し → 不明群 storeys 候補は **高4,265 / 中175 / 低21 / 判断不能521**。
  2. **低下は「階高の付け方」では直らないので、用途別階高の深掘り・定数の付け替えはしない**（下記の原因切り分けより）。主軸（階数＝高さ÷階高）は<20m（不明群の95.6%）で高精度なので据え置き。
- 根拠（再現 `uv run python -m estimate.storeys --eval`。holdout n=10,432、階高は train で学習しリーク防止）：
  - **帯別（一律3.8m）完全一致 / ±1階**：

    | 帯 | 完全一致 | ±1階 | n |
    | --- | --- | --- | --- |
    | 0-10m | 74.5% | 97.5% | 5,770 |
    | 10-20m | 56.3% | 94.0% | 3,546 |
    | 20-45m | **27.2%** | **67.0%** | 1,046 |
    | 45m+ | **14.3%** | **40.0%** | 70 |
    | 全体 | 63.2% | 92.9% | — |
    | 不明群重み | 69.5% | 95.5% | — |

    全体 63.2/92.9 は設計書 62.3/92.1 を再現。**20m以上で急落**（完全一致 27%→14%、±1階 67%→40%）。
  - **原因の切り分け**：
    - **階高の当て方は主因でない。** 近傍予測用途の階高が学習済みに無く全体中央値へフォールバックした割合は**全帯0%**（＝用途別階高を常に使えている）。真の用途を使う上限でも 全体63.7/93.5・45m+で完全20.0%止まり、「不明群相当（近傍予測用途）」は 63.1/93.1 でほぼ同じ。3方式の差は僅少。
    - **実際の階高のズレも主因でない。** 実測階高中央値（height/storeys, クリップ無し）は 0-10m 3.75 / 10-20m 3.90 / 20-45m 3.87 / 45m+ 3.60m で、全帯 3.8m とほぼ一致（差 ±0.2m 以内）。
    - → **低下は構造的**：高層ほど階数が多く小さな階高誤差が丸めで効く＋低層部の吹き抜け・機械室・屋上構造物で `measuredHeight` が実階数とずれ、ばらつきが大きい。単一除数の推定では高層帯を当てきれない（画像での実数え＝設計書 供給源が本筋だが Mapillary 被覆の問題で後回し＝2026-11-03 の判断）。
  - **不明群の高さ分布**：0-10m 79.9% / 10-20m 15.7% / 20-45m 3.9% / 45m+ 0.5%。低下する20m以上は**不明群の約4%**（残り96%は高精度帯）。信頼度を下げる対象は限定的。
- 影響範囲：`estimate/storeys.py`（`evaluate_storeys` を帯別＋原因分析に拡張、`storeys_confidence` を帯別に、reasoning 注記を20m超へ）。`make estimate` 済み。確認画面・API は変更なし（信頼度ラベルと reasoning を描画する既存経路で反映）。

## 2026-11-04 Claude 用途・階数の既知棟検証（30棟×2モデル, 5.5世代）：用途は近傍(c)を超えず

- 背景 / きっかけ：上の 2026-11-03 エントリの宿題「2モデルの精度・費用比較と採用モデル・理由は実データで走らせてから追記する」を実行。設計書「高さ20m以上で Claude の用途が近傍(c)を超えるか」で採用可否を測る。
- 前提のズレ（実行前に確認し修正）：
  - **リポジトリは Claude 4.x 世代を前提にしていたが、platform.claude.com の現行モデル一覧は「5.5 世代」**（opus-5-5 $4/$20・sonnet-5-5 $2/$10・haiku-5-5 $0.10/$0.50(≤10万tok)・fable-5-1 $10/$50）。
  - これに合わせて：`PRICING` に 5.5 の単価を追加（料金ページの値。推測しない）、既定モデルを `claude-opus-5-5` に変更し `.env` の `CLAUDE_MODEL` に明記、`eval/claude_eval.py`・`estimate/claude_infer.py` に `load_dotenv()` を追加（両者は `.env` を読んでおらず `ANTHROPIC_API_KEY` がロードされていなかった）。未知モデルのフォールバック（費用 None）は変えていない。
  - 1棟で構造化出力（`output_config.format` json_schema）が 5.5 で通ることを先に確認（通った）。
- 判断：
  - **用途は 2モデルとも高さ20m以上で近傍(c)を超えなかった**（opus-5-5 −23.3pt / haiku-5-5 −20.0pt）。設計書の分岐どおり **用途は近傍の候補提示のままとする**（Claude で用途の上積みはしない）。
  - **「根拠なく断定して外した」は両モデルとも 0 件**（conf≥0.6 の誤答ゼロ）。高層棟で用途を無理に当てず判断不能にする設計意図（推測で埋めるのは誤答より悪い）が効いている。**判断不能を安定して返しており、設計書「前提が崩れたら止める点（Claude が判断不能を返さない）」には該当しない。**
  - モデル選定：用途で有意差が出ない以上、**最安の haiku-5-5 で十分**（eval の自動推奨も haiku-5-5）。ただし下記のとおり n が小さく暫定。本運用の既定は opus-5-5 のままにし、採用は候補投入時に再検討。
- 根拠（数字・再現方法）：
  - 再現：`make claude-eval CLAUDE_EVAL_N=30 MODELS=claude-opus-5-5,claude-haiku-5-5`（holdout 既知棟・高さ20m以上から30棟。近傍分布/ベースライン/階高は train のみ＋自棟 exclude でリーク防止）。

  | モデル | 用途 正解(全体) | 用途 正解(判断分) | 用途 判断不能 | 断定誤 | 近傍(c) | 20m超 vs(c) | 階数 一致/±1 | 費用 |
  | --- | --- | --- | --- | --- | --- | --- | --- | --- |
  | claude-opus-5-5 | 10.0% | 60.0%（5棟） | 83.3% | 0 | 33.3% | **−23.3pt** | 30.0% / 60.0% | $0.2262（29棟/17,472tok） |
  | claude-haiku-5-5 | 13.3% | 57.1%（7棟） | 76.7% | 0 | 33.3% | **−20.0pt** | 30.0% / 60.0% | $0.0118（30棟/29,376tok） |

  - smoke test 1棟込みの実費用合計 ≈ **$0.254**（事前見積もり「現実的 ≈$0.43 / 最悪 ≈$1.4」の範囲内、数ドル以下）。
  - **注意（統計力）**：この近傍(c)=33.3% は「高さ20m以上の holdout n=30 サブセット」での値で、設計書の 56.8%/58.2%（holdout 全体）とは母集団が違う。Claude の全体正解率が低い主因は高い判断不能率（高層棟で用途を控える）で、「判断できた分」の 60.0%/57.1% は resolved が 5〜7棟とごく少なく低統計力。確定ではなく傾向。
  - **注意（階数）**：画像が無いため storeys は「仮の推定（高さ÷階高）」を参照値として返している色が濃く、Claude 独自判断の精度とは言い切れない。判断不能率0%はプロンプト設計（仮値を低信頼度で返してよい）どおり。
  - **注意（費用の微小な過大評価）**：`compute_cost` のキャッシュ読み乗数は 0.1× 固定だが、opus-5-5/sonnet-5-5 は実際 0.05×。システムプロンプトのキャッシュ読み分だけ僅かに過大（opus-5-5 で 1〜2 セント程度）。本運用で問題になる額ではないため今回は据え置き。
- 影響範囲：`estimate/claude_infer.py`（PRICING に 5.5 追加・DEFAULT_MODEL=opus-5-5・load_dotenv）、`eval/claude_eval.py`（load_dotenv・既定を DEFAULT_MODEL 参照に統一）、`.env`（CLAUDE_MODEL=claude-opus-5-5）、`Makefile`（比較例を 5.5 に）。用途は候補提示のまま＝第4段の出力に Claude 用途は（人の確定を経ない限り）出さない方針は不変。

## 2026-11-03 Claude 属性推定（estimate/claude_infer.py）の設計判断

- 背景 / きっかけ：設計書「第2段：推定（Claude）」の実装。画像が後回し（Mapillary 被覆が約半分、2026-11-03 の判断）でも、事実だけで用途・階数を推定し、判断不能を自己申告できる土台を先に作る。
- 判断：
  - **出力は構造化出力（Messages API の `output_config.format` の json_schema）で強制する。** usage / storeys / unresolvable の3キー。用途コードは Building_usage.xml の18コードを `enum` に入れて逸脱を防ぐ。tool use の input_schema でも同義だが、全体を JSON に固定でき reasoning を内包できる構造化出力を採った。
  - **パース失敗（壊れた JSON・refusal・テキスト無し）は1回だけリトライし、だめなら両属性を判断不能にする**（value=null / confidence=0 / unresolvable）。判断不能も candidates に1行残す（設計書「黙って行が無いのと、判断できないは別物」）。
  - **source は `image`**（Claude の多モーダル推定チャネル）。画像が無くても事実だけで動く。階数は `height` 由来（storeys.py）と別ソースで共存（1棟×属性×ソースで1行）。
  - **入力ハッシュ（モデル・プロンプト版・スキーマ・事実・画像指紋）で `data/cache/claude_infer/` にキャッシュし二重課金を防ぐ。** トークン・費用は `data/derived/claude_infer_log.jsonl` に追記。並列数は既定3、429 は SDK の自動リトライ＋バックオフ（max_retries=5）。
  - **モデルは環境変数 `CLAUDE_MODEL`**（未設定時は claude-opus-4-8）。思考は既定オフ（費用の予測性。`CLAUDE_THINKING=1` で adaptive）。
  - プロンプトは `estimate/prompts/usage_storeys_v1.md`（版番号つき）。「外観だけで断定しない／決められない属性は判断不能（推測で埋めるのは誤答より悪い）／高層の複合用途では地上POIは1階しか表さない」を明記。
  - 用途地域（設計書の入力項目）は buildings に列が無いため当面は渡さない。周辺の用途分布は近傍の既知棟から導出。
- 根拠（再現方法）：
  - ロジック（スキーマ検証・リトライ→判断不能・キャッシュ・費用計算）は API を呼ばないユニットテストで固定：`uv run pytest tests/test_claude_infer.py`（14件）。Anthropic クライアントはフェイクに差し替え、課金ゼロ。
  - **検証（先にやる）は `uv run python -m eval.claude_eval`。** 既知棟 holdout で正解率・判断不能率・「根拠なく断定して外した」件数（confidence≥0.6 で誤答）を出し、**高さ20m以上で Claude の用途が近傍(c)＝56.8% を超えるか**を測る。近傍分布・近傍ベースライン・階高はすべて train のみ＋自棟 exclude でリーク防止。屋上・道路のみ画像で階数が判断不能になるかは `--roof-image` で確認。
- 影響範囲：第2段の Claude 推定が candidates(source='image')/evidence(kind='image') に乗る。確認画面はこの reasoning を主役に出せる。**2モデルの精度・費用比較と採用モデル・理由は、`make claude-eval MODELS=...` を実データで走らせてからこの下に追記する（推測の数字は書かない）。判断不能を安定して返さない場合は設計書「前提が崩れたら止める点」に該当するので止めて報告する。**

## 2026-11-03 Phase 9 更新候補の出力：確定済みだけを CSV(BOM)/GeoJSON で出す

- 背景 / きっかけ：骨格「抽出→推定→確認・確定→出力」の最後が未実装だった。画像を後回しにして
  先に一本通す。設計書「第4段：更新候補の出力」の列定義どおり。
- 判断：
  1. **`confirmations` にある属性だけを出す**（人が確定したもの）。**未確定・判断不能は構造的に出ない**
     （confirmations を主にするため）。`tests/test_export.py` で「未確定・判断不能が1行も出ない」を固定。
  2. **CSV は UTF-8 BOM 付き**（Excel 文字化け防止）。`current_value` は raw_*（461/9999）の生値。
     建築年は `proposed_value='2022-2024'` ＋ `value_min`/`value_max` 別列。GeoJSON(EPSG:4326) も同内容＋外形。
  3. **人が修正した行は `source='manual'`・confidence/reasoning 空**（候補の値ではないため）。
     そのまま確定は candidate の source/confidence/reasoning をそのまま。→ 設計の source 集合に
     実務上 `manual` を足す扱い（出力列の意味を正直にするため）。
  4. **city_code を引数化**（既定 13104）。他自治体でも同じ処理が通ることをコードで示す（実行は新宿区のみ）。
     出力同梱 README に **PLATEAU 属性↔出力列の対応表**（uro:buildingID / bldg:usage /
     bldg:storeysAboveGround / uro:yearOfConstruction）を入れ、「公式データへの直接書き込みではない」と明記。
  5. 地図画面に **「更新候補をダウンロード」**（確定0件は無効、件数表示）。`/attribution` を整形（表・
     Mapillary 注記・ダウンロード導線）。API は `GET /api/export.csv` / `.geojson` / `/summary`。
- 根拠（動作確認）：デモ確定 9 棟 → `make export` で 9 棟 / 21 行。CSV 先頭に BOM、current=461/9999、
  proposed=ラベル、新規棟は year_built が `2022-2024`。未確定棟は出ない。`uv run pytest tests/test_export.py` 緑。
- 影響範囲：`export/update_candidates.py`・`export/__main__.py`・`export/README.md`、
  `api/main.py`（export 3 本＋attribution 整形）、`web/`（ダウンロード）、`Makefile`（export 実体化）、
  `tests/test_export.py`。出力は `data/derived/export/`（git 管理外）。

---

## 2026-11-03 画像は後回し：Mapillary 被覆が約半分（向き未考慮）→ 画像の役割を整理し直す

- 背景 / きっかけ：Mapillary カバレッジの実測（下記エントリ）を受けた方針決定。
- 判断：
  1. **画像判定は後回し。Phase 7（視錐台レイキャストでの建物割り当て）は「余力があれば実施」の位置づけに下げる。**
     主軸は引き続き **階数（高さ÷用途別階高）と建築年（版間差分）**、用途は候補提示（2026-11-03 の判定どおり）。
  2. **画像の役割を「精度向上」から「担当者が確認するための材料」に整理し直す。** 推定の素性にはせず、
     確認画面で「人が目で確かめる」ための添え物とする（当たっていれば1クリック確定を速める方向で効く）。
  3. **画像を入れるならオルソ（東京都区部オルソ 0.2m）を優先する。** 街路の実写真（Mapillary）は
     向きとカバレッジに依存するが、**オルソは屋根が必ず写る**（高層の倒れ込みは高さでバッファ）。
- 根拠（下記エントリの実測。`estimate/mapillary_coverage.py` で再現）：
  - **40m以内でデモ範囲の約半分**（高さ20m以上196棟=51.0%、西新宿1km圏567棟=48.9%）。しかも
    **これは向き（compass）未考慮の距離のみの値**で、視錐台で「実際に見えている建物」に絞ると**さらに下がる**。
  - 20m以内は 22.9%（全不明群）とさらに低い。画像を推定の主軸にできる被覆ではない。
- 影響範囲：優先度の `has_image` は当面 LOD2 の代理のまま（実写真ではない旨はUIで明記済み）。
  Phase 9（更新候補の出力）を先に通す。画像を足すのは一本通したあとの上積み。

---

## 2026-11-03 Phase 7 前段：Mapillary カバレッジ測定（不明群4,982、距離のみ）

- 背景 / きっかけ：画像判定（設計書 供給源1=Mapillary）の前に、新宿区での被覆を公開 API だけで実測。
  取得・割り当て本体（視錐台レイキャスト、画像保存）は Phase 7 で、ここでは**距離のみの被覆率**を測る。
- 方法（`uv run python -m estimate.mapillary_coverage` で再現）：
  - 不明群 4,982 棟を含むセル（経度0.005°×緯度0.004°≈450m）**120タイル**を、40m分 外側に広げて
    **images の bbox 検索で一括取得**（Graph API v4, `Authorization: OAuth`。**1棟1リクエストにしない**）。
    fields は `id, computed_geometry, computed_compass_angle, camera_type, captured_at` のみ。
    取得 **134,864 画像 / 120 リクエスト / 約708秒**。各棟の重心（EPSG:6677）から最近傍画像までの距離で判定。
- 結果：
  - **半径20m以内に画像: 22.9%（1,139/4,982）／半径40m以内: 47.1%（2,349/4,982）。**
  - **高さ帯別（20m/40m）**：0-10m 21.6%/46.2%、10-20m 26.3%/47.9%、20-45m 27.4%/49.7%、
    45m+ 33.3%/61.9%、高さ無 25.1%/51.1%。**高い棟ほど被覆が良い**（街路から見通しやすい）。
  - **撮影年**：2016以降が中心、ピークは **2021（488）**、2022–2025 も厚い（361/332/146/183）。
    2006–2015 の古い画像も少数あり、**建て替え前を写している可能性**がある（新規棟の年代判定では要注意）。
  - **camera_type（40m被覆棟の最近傍 / 取得全体）**：perspective 2,097 / 124,574、
    equirectangular 139 / 5,403、spherical 109 / 4,738、fisheye 4 / 149。
    **360°（spherical＋equirectangular）は最近傍の約10.5%**。この帯は切り出し・投影の処理が別になる。
  - **デモ範囲**：高さ20m以上(196) 20m 28.1%/40m 51.0%、西新宿1km圏(567) 24.9%/48.9%、
    西新宿かつ20m以上(21) 52.4%/66.7%。**デモで見せやすい西新宿の高層帯は 40mで約2/3**。
- 注意：**距離のみの被覆率で、向き（compass）は考慮していない。実際にその建物が写っている保証はない。**
  視錐台で「見えている建物」に絞ると、この値より下がる（Phase 7 で実測）。
- 影響範囲：`estimate/mapillary_coverage.py`（測定のみ。取得・割り当て本体は未実装）。
  結果 JSON は `data/derived/mapillary_coverage.json`（git 管理外）。優先度の `has_image` は当面 LOD2 の代理、
  Mapillary を素性に使うなら 40m・向き考慮で付け直す（Phase 7）。

---

## 2026-11-03 Phase 7 前提：Mapillary トークンが無く、カバレッジ測定は保留（画像バッジは実写真でない）

- 背景 / きっかけ：画像の取得・割り当て本体（Phase 7）の前に、Mapillary のカバレッジを
  公開 API（Graph API v4, images の bbox 検索）だけで測ろうとした。先に前提を確認した。
- 判断（＝ここで止める）：
  1. **`.env` が存在せず `MAPILLARY_ACCESS_TOKEN` も未設定** → **カバレッジ測定は実行しない**。
     `.env.example` の `MAPILLARY_ACCESS_TOKEN=` に値を入れてから再開する（設計書・ハッカソン規約どおり、
     公開API以外では取得しない＝スクレイピング禁止）。
  2. **優先度順リストの「画像」バッジは実写真ではない。** 根拠は `buildings.lod_type == 'LOD2'`
     （`api/priority.py` の `has_image`）＝3D都市モデルの面テクスチャの有無であり、街路の実写真ではない。
     **誤解を避けるためラベルを「LOD2」に変更**（バッジ `title` と確認カードの画像枠に
     「実写真は未取得（Phase 7）」と明記）。優先度式の `has_image` も当面 LOD2 の代理であることを維持。
- 測定の設計（トークン入手後に実行する。コードはまだ書かない）：
  - 不明群 4,982 の重心（EPSG:6677）から半径 20m/40m に Mapillary 画像があるかを数える。
    全件が重ければ**優先度上位 300 棟**で測り件数を明記。**images の bbox 検索でまとめて取得**し
    ローカルで棟に対応づける（1棟1リクエストにしない）。fields は
    `id, computed_geometry, computed_compass_angle, camera_type, captured_at` のみ。
  - 報告は高さ帯別に：20m/40m 被覆率、撮影年分布、camera_type（spherical か）内訳、
    デモ範囲（高さ20m以上 196 / 西新宿1km圏 567）の被覆率。**向きは考慮せず距離だけ**で、
    実際に建物が写っている保証はない旨を明記する。
- 影響範囲：`web/app.js`・`web/index.html`（バッジ/画像枠のラベル）。測定本体は未実装。
  あわせて、確認カードが縦に収まらない時に中身が隠れていた不具合を修正（`#confirm-scroll` を
  `overflow-y:auto; min-height:0` にし、ボタンはフッターに固定。基本は1画面、はみ出す分だけ中でスクロール）。

---

## 2026-11-03 Phase 5 版間差分：建物IDは版間で安定（IoU中央値1.000）→ ID差分を採用

- 背景 / きっかけ：2023年度版（`$DATA_DIR/raw/plateau/2023/`）を取り込み、「新しく現れた棟」を
  検出する。設計書「新しく現れた建物の検出」の要検証点＝**建物IDが版をまたいで安定か**を先に確かめる。
- 判断：
  1. **2023年度版は buildings を壊さず別テーブル `buildings_2023` に取り込む**
     （`ingest/raw_version.py`。センチネル正規化・外形は 2025 と同じ処理を再利用）。
     **2023年度版は IUR uro 3.1、2025年度版は 3.2** と名前空間が違うため、`ingest/citygml.py` の
     uro 要素は localname（`{*}` ワイルドカード）で判定するよう変更（版非依存）。最初の取り込みで
     0棟になり発覚 → 修正後 57,507 棟。
  2. **建物IDは版間で安定 → ID差分を採用**（外形IoU照合は不要）。閾値の決め方と理由は下記。
  3. 新規棟（2025のみID）に **year_built 候補（value_min=2022, value_max=2024, source='version_diff'）**
     を投入。年の区間は両版の調査年の間（2023年度版に無く2025年度版で初出現）から導く。
- 根拠（`uv run python -m estimate.version_diff --report` で再現）：
  - **ID重なり**：共通ID **51,975** / 2025のみ **5,805** / 2023のみ **5,532**。
  - **共通IDの外形IoU（EPSG:6677, n=51,975）**：中央値 **1.000** / 平均 1.000 /
    **IoU<0.5 は 0.0%** / IoU≥0.9 は 100.0%。→ IDと外形は1:1で完全安定。
    **判定しきい値**：中央値≥0.9 かつ IoU<0.5 が 5%未満なら「ID安定」。実測はこれを大きく満たす
    （分布が 1.0 に集中。もし不安定なら IoU<0.5 の谷に閾値を置いて照合する設計）。
  - **creationDate 判定との重なり（設計書「5,796中4,009が2021属性」の確認）**：
    creationDate=2026-03-13 は **5,796**（うち ID差分でも新規なのは 5,796 ＝全件が新規ID）。
    そのうち **survey_year=2021 は 4,009**（＝形状の作り直し等で旧属性を引き継ぐ棟）。**設計書と完全一致。**
    → creationDate 単独は「作り直し」を新築と誤検出しうるが、ID差分と組み合わせると
    「新規IDかつ2021属性」= 4,009 を切り分けられる。
  - **新規棟 5,805 のうち不明群(is_target)は 1,788**。year_built 候補は新規棟 5,805 件に投入。
- 影響範囲：`ingest/raw_version.py`（版別ingest）、`ingest/citygml.py`（uro版差の吸収）、
  `estimate/version_diff.py`（検証＋候補投入）。`buildings_2023` テーブルは版間差分の参照専用。

## 2026-11-03 Phase 5 優先度スコア：高さ>新規>面積>画像 の加重和

- 背景 / きっかけ：不明群 4,982 を同列で見ると担当者が迷う。設計書「優先度の付け方」の順
  （高さ→新規出現→外形面積→画像）を単一の式にして `/api/targets` を差し替える。
- 判断：**各要素を[0,1]に正規化し、重みを段階的に下げた加重和**にする（重みの大小＝設計書の優先順）。

  ```
  score = 0.60·height_n + 0.25·is_new + 0.10·area_n + 0.05·has_image
  height_n = min(height,100)/100   area_n = min(area_m2,1000)/1000
  is_new   = 版間差分で新規なら1    has_image = LOD2テクスチャありなら1（Mapillaryは未実装）
  ```

  確定済みの棟は後ろに回す（`ORDER BY confirmed ASC, score DESC`）。式は `api/priority.py`。
- 根拠（`demo_counts`。`/api/demo` で再現）：
  - **デモ範囲**：高さ20m以上の不明群 = **196**（設計書と一致）。
    西新宿（都庁 139.6917,35.6896 から半径1km, EPSG:6677）に絞ると不明群 **567**、うち20m以上 **21**。
  - 並びの先頭は高層（146m, 119m…）→ 新規かつ中層 → という順になり、設計書の意図どおり。
- 影響範囲：`api/priority.py`、`/api/targets`。画像有無は当面 LOD2 で代用（Mapillary 実装時に差し替え）。

## 2026-11-03 Phase 4 階数の仮推定：用途別階高を学習（flat3.8で 63.2%/92.9%、設計書再現）

- 背景 / きっかけ：確認画面を立てるために、まず仮の推定で candidates を埋める（設計書「UIを前に」）。
  階数は高さ÷階高で自動で埋め、人は確認のみ（設計書）。
- 判断：
  1. **階高を既知棟から学習**（median(height/storeys)、2.5〜6.0mにクリップ）。不明群は近傍で推した
     用途の階高を使い、無ければ全体中央値。**高さ45m超は reasoning に「高層のため誤差が大きい」と注記し
     信頼度も下げる**。source='height'。高さ欠損の棟は storeys を**判断不能**で残す（value NULL, 行は残す）。
  2. **用途は近傍＋高さ±3m の候補提示**（source='neighbor', k=5）。信頼度は低〜中で、**高さ20m以上は「低」**。
     POI/OSM建物タグは **evidence** に入れ、主用途が住宅系で1階に店舗/事務所POIがある棟に
     **「併用住宅の可能性」フラグ**（2026-11-03 の判定どおり。177棟）。
- 根拠（`uv run python -m estimate.storeys --eval`、holdout n=10,432）：
  - **学習した階高 vs 設計書**：411 3.80（3.80）/ 412 3.73（3.70）/ 401 3.99（3.96）/ 422 4.27（4.47）。
    全体中央値 3.80m。422 は -0.20 ずれるが n が小さい。
  - **階数の精度**：一律3.8m で **完全一致 63.2% / ±1階 92.9%**（**設計書 62.3% / 92.1% を再現**）。
    用途別階高にすると 63.7% / 93.5% と僅かに改善。
  - 投入：不明群 4,982 に candidates 9,964 行（usage=neighbor / storeys=height）、evidence 2,552 行。
- 影響範囲：`estimate/storeys.py`。candidates/evidence は `api/schema.py` のテーブル。

## 2026-11-03 Phase 4 確認・確定と計測：opened_at はサーバー側、そのまま/修正はボタンで分ける

- 背景 / きっかけ：設計書「処理時間の計測は後付けできない」。確認画面と同時に計測を入れる。
- 判断：
  1. **candidates / confirmations / evidence を設計書どおり作り**（`api/schema.py`、buildings を触らない別スクリプト）、
     計測用に **open_events**（建物を開いた時刻をサーバー側で保持）を追加。
  2. **opened_at は `POST /api/open/{id}` でサーバーの時計で記録**、confirmed_at は確定時に now()。
     クライアントの時刻に依存しない。`/api/metrics` は**1棟ごとに集計**し、**中央値**（平均は使わない）。
  3. **「そのまま確定」と「修正して確定」は押したボタンで分ける**（usage の accepted_candidate に反映）。
     確定後は**次の優先棟を自動で開く**（連続処理をデモで見せる）。
  4. 画面は1画面・スクロールなし。**信頼度は数値を出さず高・中・低**、**根拠の文章を主役**にする。
     unresolvable は警告色で理由を出す。Enter でそのまま確定。
  5. **`make reset-confirmations`** で確定履歴と計測だけ消す（candidates/buildings は残す。撮り直し用）。
- 根拠：`uv run uvicorn api.main:app` → `/api/open` `/api/confirm` `/api/metrics` の往復を確認。
  用途コードは `Building_usage.xml` から読む（461=不明は選択肢から除外、17コード）。
- 影響範囲：`api/schema.py`、`api/db.py`（共有RW接続）、`api/main.py`（open/confirm/metrics/targets/usage_codes）、
  `api/codelists.py`、`web/`（確認カード・計測表示）。

---

## 2026-11-03 Phase 3 地図UI：建物ポリゴンは GeoJSON ＋ gzip で配信する

- 背景 / きっかけ：全 57,780 棟を地図に出すため、配信フォーマットを決める必要があった
  （設計書の指示：「まずサイズと描画速度を測り、重ければ PMTiles か FlatGeobuf に切り替える」）。
- 判断：**GeoJSON 一括配信 ＋ gzip（`GZipMiddleware`）で進める。** PMTiles/FlatGeobuf への変換は
  いまは入れない（描画が実機で重いと分かった時点で切り替える）。
- 根拠（実測。`api/build_geojson.py` と同じ SQL で計測）：
  - プロパティを id / is_target / height_m / usage に絞った GeoJSON は **生 28.1MB / gzip 6.7MB（4.2倍）**、
    生成 ~3.3s。実配信（`/data/buildings.geojson`）の gzip 転送は約 6.6MB。
  - localhost 配信 6.6MB ＋ MapLibre の `fill-extrusion` は GPU 描画で 5.7万ポリゴンは実用域。
    ビルド工程（tippecanoe 等の外部ツール）を増やさずに済む利点が大きい。
- 影響範囲：生成物は `data/derived/buildings.geojson`（git 管理外、起動時に無ければ自動生成）。
  描画が重い端末が出たら、同じ属性で FlatGeobuf 化（`/data` 配信）または PMTiles 化に切り替える。

---

## 2026-11-03 Phase 2 判定の採用：用途は「候補提示」にとどめる

- 背景 / きっかけ：Phase 2 後半の実測（下記エントリ）を受けて、設計書「Day 1 の分岐」で
  **「超えない」側を採用**することを決定した。
- **決定：**
  1. **用途は「候補提示」にとどめる。** 仮の推定は **近傍＋高さ±3m（source='neighbor'）**。
     **高さ 20m 以上は信頼度「低」**（帯別で近傍が 47% 前後まで落ちるため、下記）。
  2. **POI と OSM 建物タグは推定値にしない。evidence に入れる。** あわせて
     **「併用住宅の可能性」フラグ**（1階に店舗・事務所の POI があり、主用途は住宅系）を立てる。
  3. **主軸は階数（高さ÷用途別階高）と建築年（版間差分の区間）。** 用途は **Phase 6** で、
     **既知棟の高さ 20m 以上で Claude の推定が近傍(c)を超えるか**を測り、超えれば採用する。
  4. どちらに転んでも骨格（絞り込み → 地図 → 確定 → 出力）は成立する（設計書どおり）。

### 併用住宅の発見（数字つき。POI は「1階の使われ方」、PLATEAU は「建物の主用途」）

- POI があると、その棟が **併用住宅(413/414)** である確率が上がる（`eval/poi_eval.py` 由来の
  再計算）：

  | 母集団 | 併用住宅(413/414) の割合 |
  | --- | --- |
  | holdout 全体（10,432 棟） | 9.6%（1,006 棟） |
  | **POI あり（1,905 棟）** | **22.1%（421 棟）** |
  | POI なし（8,527 棟） | 6.9% |

  → **POI があると併用住宅である確率は全体の約 2.3 倍。**
- さらに **POI あり棟の 52.2% は真の主用途が住宅系(411–415)**。つまり POI は「1階に店・事務所が
  ある」ことは正しく示すが、PLATEAU の用途コードは建物の**主用途**（多くは住宅系/併用住宅）なので、
  POI→商業(402) と直接当てると外れる。**この POI を『併用住宅フラグ』＋evidence として使うのが正しい。**

### 確認：不明群重み 62.3% は設計書の階数ベースライン 62.3% と同じ数字か（偶然か）

- 設計書 line 19/170 の **62.3% は「階数(storeys)」のベースライン**（高さ÷3.8m で階数が当たる率、±1階で92.1%）。
- 本フェーズの **62.3%（正確には 62.26%）は「用途(usage)」の一致率**を不明群の高さ帯構成比で加重した値。
  入力は `buildings.usage` と `eval_split`、式は帯別用途正解率（0-10m 65.55% / 10-20m 49.75% /
  20-45m 47.13% / 45m+ 45.71%）× 帯別重み（79.94 / 15.67 / 3.92 / 0.47%）の加重平均。
- **両者は量も入力テーブルも式も別物**（階数 vs 用途）。コードに `62.3` のハードコードは無く、設計書の
  数字を流用してもいない（`grep` で確認）。**一致は偶然**と記録する。修正は不要。

---

## 2026-11-03 Phase 2 後半：POI 結合は用途推定の主軸にならない（Day 1 分岐の判定）

- 背景 / きっかけ：Phase 2 前半で近傍＋高さが既に ~58% と分かったため、設計書
  「Day 1 の分岐（POI 結合で 56.8% を超えるか）」を、**近傍の最良（58.2%）を
  物差し**に、高さ帯別・不明群重み付きで実測した。結論が出たので報告して止まる。
- **判断（提案。採否は要相談）：POI 結合を用途推定の主軸にしない。**

### step 0：評価リークの防止（`tests/test_eval_leak.py`）

- 近傍モデルは **train（ホールドアウト以外の既知棟）だけ** から作る。ホールドアウト棟・
  不明群は候補に入らない。候補 building_id が全て train、かつ自分自身を含まないことを
  テストで固定（`uv run python -m pytest tests/test_eval_leak.py`）。
- POI 予測は building_id 由来の POI だけを使い、その棟自身の `usage` を参照しない
  （`estimate.poi` は usage を受け取らない）。

### step 1：ベースライン 高さ帯別（`eval/baselines.py run_bands`、holdout=10,432、近傍 k=5）

| 帯 | holdout | 不明群 | (a)最頻 | (b)近傍 | (c)+高さ |
| --- | --- | --- | --- | --- | --- |
| 0-10m | 5,770 | 3,566 | 59.7% | 64.1% | 65.5% |
| 10-20m | 3,546 | 699 | 31.3% | 49.1% | 49.7% |
| 20-45m | 1,046 | 175 | 1.2% | 46.6% | 47.1% |
| 45m+ | 70 | 21 | 0.0% | 45.7% | 45.7% |
| 全体 | 10,432 | 4,461 | 43.8% | 57.1% | 58.2% |
| **不明群重み** | | | 52.6% | 60.9% | **62.3%** |

- 重み＝不明群の高さ帯構成比（0-10m=80% / 10-20m=16% / 20-45m=4% / 45m+=0%）。不明群は
  低層に強く偏るため、**不明群に近い条件での近傍の実力は ~62%** と、holdout 全体(58%)より高い。
- **設計書 43.1/49.5/56.8% を再現できなかった理由**：(a) 43.8% は 43.1% とほぼ一致。
  (b) は定義依存で、設計書の 49.5% は「半径200mまで薄めた」値（前半で実測）。帯別で見ると
  (b) は 0-10m で 64%、高層帯で 46〜49% まで落ちる＝**設計書 49.5% は高層帯・希釈時の値に相当**し、
  タイト近傍の低層では 64% まで当たる。(c) 58.2% は 56.8% に近い。**近い棟は用途が似るため、
  用途は近傍だけでほぼ決まり、高さ±3m の上乗せは僅か（(b)→(c) で +1.1pt）。**

### step 3：POI 結合の効き目（`eval/poi_eval.py`、holdout）

POI 割り当て（`estimate/poi.py`）：POI 総数 294,935（Overture 82,431／OSM POI 29,277／OSM 建物
183,227）。内包＝距離0 を含む 10m 以内で 89,716 件を割り当て。用途予測が付いた建物は poi 17.3%／
osm_building 20.8%（全 57,780 棟に対して）。

| 帯 | 近傍(c) | POIのみ | 合成(POI→近傍) | 上積み | OSM建物(付) | 合成+OSM |
| --- | --- | --- | --- | --- | --- | --- |
| 0-10m | 65.5% | 27.8% | 64.1% | **-1.4** | 92.2% | 69.3% |
| 10-20m | 49.7% | 27.2% | 45.5% | **-4.3** | 77.4% | 50.7% |
| 20-45m | 47.1% | 22.6% | 33.3% | **-13.9** | 64.9% | 34.4% |
| 45m+ | 45.7% | 21.3% | 28.6% | **-17.1** | 64.3% | 30.0% |
| 全体 | 58.2% | 25.6% | 54.5% | **-3.7** | 85.4% | 59.2% |
| 不明群重み | 62.3% | 27.4% | 59.8% | **-2.4** | 88.6% | 64.8% |

- **POI を直接用途にすると精度が下がる。** 合成（POIあり→POI、なし→近傍）は全帯で近傍を下回り、
  POI 被覆が高い高層帯ほど悪化（20-45m で -13.9、45m+ で -17.1pt）。
- **原因は併用住宅。** POI あり holdout 棟（1,905）の真の用途は 店住(413) 421／共住(412) 399／
  業務(401) 395／商業(402) 295／住宅(411) 162 と混在。最大は **413 店舗等併用住宅**。地上 POI は
  「1階に店がある」ことは正しく示すが、PLATEAU の主用途コードは 413/412 であり、POI→商業(402) は
  系統的に外す（POI=402 と予測した 1,260 棟の真値は 店住327/商業273/業務262/共住250…）。
- **OSM 建物タグ（source=osm_building）は素性としては有効**（付いた棟で 85.4%、0-10m で 92.2%）。
  house→411／apartments→412 が住宅系に正しく当たるため。ただし合成への寄与は被覆が低く +1.0pt 止まり。

### step 3：不明群(is_target)での被覆率（ここが決定的）

| 帯 | 棟数 | POI | OSM建物 | いずれか |
| --- | --- | --- | --- | --- |
| 0-10m | 3,566 | 5.8% | 2.0% | 7.6% |
| 10-20m | 699 | 12.0% | 2.6% | 14.3% |
| 20-45m | 175 | 26.3% | 5.1% | 29.7% |
| 45m+ | 21 | 19.0% | 14.3% | 23.8% |
| 高さ無 | 521 | 5.8% | 0.2% | 6.0% |
| 全体 | 4,982 | 7.5% | 2.0% | 9.2% |

- **不明群では POI/OSM がほとんど付かない**（いずれか 9.2%）。不明群は低層(0-10m)に 80% が偏り、
  そこでの被覆は 7.6%。POI が比較的付く 20m+ でも棟数が少なく(196)、しかもそこは POI が最も精度を
  落とす帯。**POI は「効く相手が少なく、効く帯で外す」。**

### step 4：設計書の分岐表に沿った判定（`docs/design.md`「Day 1 に判断すること」）

- 分岐表の基準「POI 結合で 56.8% を超えるか」に対し、**近傍の実力 58.2%（不明群重み 62.3%）を
  物差しにすると、POI 結合は超えない**（むしろ下げる）。先行研究の「周辺ラベル 56%」は近傍に相当し、
  それは既に満たしている。設計書の「POI の直接結合は効く」は本データ・本タスク（主用途1コード）では
  再現されない。
- **提案（採否は相談）：**
  1. 用途推定の主軸は **近傍＋高さ(58%／不明群62%)** のまま。POI は用途の直接推定に使わない。
  2. 分岐表の「超えない」側＝**「用途は候補提示にとどめ、階数・建築年で勝負する」** を採る。
  3. POI/OSM は捨てず、**evidence（根拠表示）と「併用住宅(413/414)の検出フラグ」** として使う
     （審査で「なぜ候補か」を見せる材料。精度指標の主軸にはしない）。
  4. 画像判定（Phase 8）は「上乗せ候補」として Day 2〜4 に別途比較する。
- **次の一手は私（ユーザー）の決定待ち。これ以上の実装（POI 主軸化／画像切替／候補提示 UI）は進めない。**

- 影響範囲：`eval/common.py`（高さ帯・近傍モデル）、`eval/baselines.py`（run_bands）、
  `eval/poi_eval.py`、`estimate/poi.py`＋`estimate/poi_mapping.yaml`、`tests/test_eval_leak.py` を追加。
  再現：`uv run python -m eval.baselines` / `uv run python -m eval.poi_eval` / `uv run python -m estimate.poi`。

---

## 2026-11-03 source の定義に osm_building を追加（design と実装の整合）

- 背景 / きっかけ：ライセンス方針（`docs/attribution.md`）で「`source` が `poi` / `osm_building` の行は OSM 由来で ODbL 対象」と書いたが、設計書の `source` 列には `osm_building` が無く、CSV 側には `neighbor` も無かった。帰属表示・ライセンスの判定を source で機械的に当てるには、定義をそろえる必要がある。
- 判断：
  - candidates の `source` に **`osm_building`** を追加（`poi` / `osm_building` / `image` / `neighbor` / `height` / `version_diff`）。
  - 更新候補リスト（CSV）の `source` に **`neighbor`** と **`osm_building`** を追加。
  - 変更箇所に「2026-11-03 追記」を明記（`docs/design.md`）。
- 根拠（理由）：**OSM の `building` タグ（apartments/commercial/office 等）を POI とは別のソースとして評価するため。** 2026-11-02 の分離（`osm_poi.geojson` 29,277 件／`osm_buildings.geojson` 183,227 件）で building タグは POI とは別物として保存済み。推定での寄与と ODbL 対象範囲を source 単位で区別できるようにする。
- 影響範囲：
  - ODbL の対象は `source ∈ {poi, osm_building}` の行。`neighbor` は PLATEAU 既知棟（近傍）由来なので OSM の対象ではない。`height` / `version_diff` も PLATEAU 由来（`docs/attribution.md` に1行補足済み）。
  - Phase 2 後半の POI 結合（`estimate/poi.py`）で building タグを付けるときは `source='osm_building'` を使う。

---

## 2026-11-03 Phase 2 前半：既知棟の定義・ホールドアウト・ベースライン（POI結合なし）

- 背景 / きっかけ：POI 結合の前に、用途推定のベースラインを既知棟のホールドアウトで実測し、Day 1 判定（56.8% を超えるか）の土俵を固めた。

### 既知棟の定義と 52,161 / 52,798 の差

- **既知棟 ＝ usage 非NULL（461以外）かつ height 非NULL ＝ 52,161**（設計書と一致）を採用。
- usage のみ既知は 52,798。差 **637 棟は height=-9999（高さ欠損）の既知棟**。height=-9999 は全体 1,158（既知 637 ＋ 不明群 521）で一致。
- 高さベースラインに height が要るため、母集団は 52,161 に統一。

### ホールドアウト

- seed=42 で 20%。train 41,729 / holdout 10,432。`eval_split` テーブルに保存し、後フェーズで使い回す（`eval/holdout.py`、`uv run python -m eval.holdout`）。

### ベースライン実測（holdout=10,432。`eval/baselines.py`）

| 段階 | 採用した近傍定義 | 実測 | 設計書 |
| --- | --- | --- | --- |
| (a) 最頻値 [411] | — | **43.8%** | 43.1% |
| (b) 近傍の用途 | 半径200m | **49.0%** | 49.5% |
| (c) 近傍 ＋ 高さ±3m | 半径150m | **56.9%** | 56.8% |

試した近傍定義（正解率）：
- (b) 半径 25/50/100/150/200m = 56.1 / 54.3 / 51.7 / 50.1 / 49.0%、k=1/3/5/10/20 = 57.9 / 57.3 / 57.1 / 56.0 / 53.9%
- (c) 半径 25/50/100/150/200m = 57.5 / 58.1 / 57.2 / 56.9 / 56.6%、k=1/3/5/10/20 = 57.9 / 57.9 / 58.2 / 57.8 / 57.8%

### 重要な気づき（要注意）

- **近傍単独（タイトな定義）で既に ~57-58%**（k=1 で 57.9%、半径25m で 56.1%）に達する。設計書の「(b) 49.5%」は**半径200mまで薄めたときの値**で、タイト近傍はもっと当たる。
- **高さ±3m の上乗せはごく僅か**（(b) 最良 57.9% → (c) 最良 58.2%、+0.3pt 程度）。設計書の 49.5→56.8 という増分は、本データのタイト近傍では再現されない（空間的に近い棟は用途が似ており、近傍だけで用途がほぼ決まるため）。
- **設計書値に一番近い定義を「採用」したのは、設計書との対照のための便宜**。用途推定の実力としては近傍＋高さで **~58%** とみるのが妥当。

### 不明群での近傍の使えるさ（is_target 4,982 の近傍にある既知棟数）

- 半径50m：中央値 25 / 平均 26.0 / 最大 90、既知棟 0 件の不明群は **52棟（1.0%）**
- 半径100m：中央値 101 / 平均 100.2、既知棟 0 件は **4棟（0.1%）**
- → 近傍法は不明群でほぼ常に適用可能（孤立棟はごく僅か）。

### Day 1 判定への含意（次フェーズで判断）

- 近傍＋高さベースラインが既に **~58%** あるため、**POI 結合は ~58% を上回って初めて主軸にする価値がある**。56.8% を基準にすると近傍単独で満たしてしまう点に注意。POI 結合は未実装（次フェーズで判断待ち）。

## 2026-11-02 ingest 実行（2025年度版）：不明群 4,982 を抽出し設計書値と全一致

- 背景 / きっかけ：`uv run python -m ingest --version 2025` を実行し、DuckDB `data/machikarte.duckdb` に buildings を構築。
- 判断：センチネル正規化・不明群判定・targets.geojson 出力まで一本通した。is_target は `raw_usage='461'`（4条件が全て 4,982 で一致したうえで立てた）。
- 根拠（実測。コードで再現可能）：

  | 指標 | 実測 | 設計書 | 判定 |
  | --- | --- | --- | --- |
  | 全棟数（13104） | 57,780 | 57,780 | 一致 |
  | 不明群（is_target） | 4,982 | 4,982 | 一致 |
  | 削減率 | 91.4% | 91.4% | 一致 |
  | 高さ10m以上 | 895 | 895 | 一致 |
  | 高さ20m以上 | 196 | 196 | 一致 |
  | 高さ45m以上 | 21 | 21 | 一致 |
  | 高さ100m以上 | 6 | 6 | 一致 |

  - センチネル件数：usage=461 / detailed_usage=0 / storeys=9999 / survey_year=0001 / roof_edge_area=-9999 は**いずれも 4,982**（roof_edge_area の欠損が不明群と完全一致）。height=-9999 は 1,158。
  - `roof_edge_area` の XPath は `uro:buildingDetailAttribute/uro:BuildingDetailAttribute/uro:buildingRoofEdgeArea`。
  - センチネル `1011` は `uro:fireproofStructureType`（耐火構造不明）の値で、保持列（usage/detailed_usage）のセンチネルではないと特定（保持列のセンチネルは 461 と 0 のみ）。
  - 外形：EPSG:4326（表示用, lon/lat）と EPSG:6677（幾何演算用, メートル）の両方を保持。不明群 4,982 を `data/derived/targets.geojson`（EPSG:4326）に出力。
- 影響範囲：第1段「絞り込み」が設計書どおり再現できた。地図UI（Day1–2）の入力は targets.geojson。pytest（`tests/test_ingest.py`）で正規化・raw保持・座標変換・city_code フィルタを固定。

## 2026-11-02 ingest 着手前の CityGML 検証（全31ファイル・2025年度版）

- 背景 / きっかけ：1タイルの XPath だけで ingest を書かず、全ファイルで属性の実在とセンチネル、版の正しさを先に確認した。
- 判断：XPath は IUR 3.2 系（`uro:buildingDetailAttribute/uro:BuildingDetailAttribute/...`）で確定。建物抽出は buildingID 接頭コード `13104` で絞る。
- 根拠（全31ファイルを `lxml.iterparse` で走査、`data/raw/plateau/2025/udx/bldg/`）：
  - タイル内の総棟数は 100,663 だが、メッシュに隣接区が混在（13114:14,580／13116:9,344／13105:7,904／13113:7,242／13101:2,032／13103:1,469／13120:312）。**13104 のみで 57,780 棟＝設計書と一致。**
  - 13104 で各属性の「見つからない棟」は全て **0**（usage/detailedUsage/storeysAboveGround/surveyYear/measuredHeight/creationDate/buildingID）。停止条件に該当せず。
  - センチネル件数（13104）：usage=461 → 4,982／detailedUsage=0 → 4,982／storeysAboveGround=9999 → 4,982／surveyYear=0001 → 4,982／measuredHeight=-9999 → 1,158。**4条件の AND も 4,982**（設計書「6条件が同じ 4,982 を指す」と一致）。
  - creationDate 分布（13104）：2024-03-15 → 51,984、**2026-03-13 → 5,796**（設計書の想定と完全一致 → 落とした版は正しい）。XPath 確認サンプル `53394517_bldg_6697_op.gml` が 2024-03-15 だったのは、2025年度版内に旧棟の作成日が引き継がれているため。
  - `buildingIDAttribute` / `buildingDetailAttribute` を1棟に複数持つ建物は **0**（最大1）。`bldg:usage` の複数保持も 0。
- 影響範囲：ingest は 13104 フィルタ必須。センチネル正規化と不明群判定の基準値が実データで裏取りできた。

## 2026-11-02 外形は lod0RoofEdge を採用（lod0FootPrint は存在せず）

- 判断：建物外形は `bldg:lod0RoofEdge` を使う。
- 根拠：全31ファイルで `bldg:lod0FootPrint` は **1棟も存在しない**（0 出現）。LOD1 は全棟 `bldg:lod1Solid`、LOD2 は一部のみ（`lod2Solid`/`lod2MultiSurface`）。
- 注記：**lod0RoofEdge は「屋根の縁」なので、建物の足元（footprint）より一回り大きめに出る。** 面積・内包判定で過大評価方向の誤差があり得る点に留意（POI の内包判定が緩めに当たる方向）。

## 2026-11-02 OSM を POI と建物に分離

- 判断：Overpass 取得結果を、用途推定に効く POI と、材料として残す建物に分けて保存。
- 根拠 / 内訳：amenity/shop/office/tourism/leisure/healthcare のいずれかを持つ 29,277 件 → `osm_poi.geojson`。`building` タグのみの 183,227 件 → `osm_buildings.geojson`。building 値の上位は yes:115,246／house:37,855／apartments:18,826／residential:5,577／commercial:1,414／public:1,093／retail:666／office:310／hotel:65 等。
- 影響範囲：POI 結合は osm_poi を主に使い、building 値（apartments/commercial/office 等）は補助特徴量にできる。

## 2026-11-02 リポジトリ構成を作成

- 背景 / きっかけ：設計書「リポジトリ構成」に沿って初期ディレクトリを用意した。
- 判断：処理コードは書かず、ディレクトリと雛形（README・`__init__.py`・`.gitignore` 等）のみ作成。
- 根拠：実装順序 Day 1 の「リポジトリ作成」に相当。
- 影響範囲：全体の土台。設計書正本は `docs/design.md`。
