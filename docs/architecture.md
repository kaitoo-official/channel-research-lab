# Architecture — YouTube Channel Analyze

```
人（ブラウザで普通に視聴・NotebookLM に手で登録・メモを書く）
   │  チャンネルURL / 動画URLリスト / メモ / 確認済み要約 / 作者記事 / 許可のある字幕
   ▼
ytca（Python 標準ライブラリのみ・通信なし）
   urls.py       URL の検証・正規化（@handle は %エンコード・NFC を統一、/channel/UC…、/c/ /user/ は拒否）
   store.py      channel.json / videos.json（項目ごとに出所 human|api と日時）/ ロック / ジャーナル / API 項目の期限削除
   materials.py  5 種類の材料（basis：AI_SUMMARY / HUMAN_NOTES / CREATOR_ARTICLE / AUTHORIZED_TRANSCRIPT / NONE）
   analysis.py   1 本分の CLAIM/EVIDENCE/PRINCIPLE/TEST を検証（/yt-analyze と同じ規則＋材料の参照必須）
   synthesis.py  動画横断：テーマ統合・繰り返し原則（2本以上）・見解の分岐と時系列・検証候補・網羅状況
   report.py     Markdown レポート
   batch.py      マニフェストで一括取り込み・途中失敗からの再開（journal.json）
   ▲
Claude（このプロジェクトの Skill）：材料を読み → ytca-extract で 1 本分の分析 JSON を作る → ytca が検証して保存
```

## データ（`data/channels/<key>/`）
| ファイル | 中身 |
|---|---|
| channel.json | 正規化した URL・handle / channel_id・動画一覧の完全性（人の確認者・日時・メモ） |
| videos.json | 動画ごと：URL・状態（UNANALYZED / PARTIAL / ANALYZED / ON_HOLD / BLOCKED）・項目（値・出所・日時）・材料 ID |
| materials/<video>/<Mxx>.json | 材料本文・種類・basis・coverage（FULL / PARTIAL / UNKNOWN）・確認/許可メモ・ハッシュ |
| analyses/<video>.json | 検証済みの CLAIM / EVIDENCE / PRINCIPLE / TEST・方針による格下げの記録 |
| synthesis.json / report.md | 横断分析の結果 |
| journal.json / lock.json | 一括取り込みの進み具合・同時実行の防止 |

## /yt-analyze との関係
- 再利用したもの（同じ規則を ytca に実装・既存コードは変更していない）：CLAIM / EVIDENCE / PRINCIPLE / TEST の形、CONFIRMED の条件（独立した一次情報・作者本人のページは不可）、PRINCIPLE=HYPOTHESIS、TEST ≤ 3・変数 1 つ・kept_constant 必須、外部検証 ≤ 5、60 文字以上の連続一致の禁止、カテゴリ一覧。
- 再利用しないもの：字幕の取得（`yt-acquire` / `yt-acquire-chrome` のブラウザ操作、`--paste` の全文保存）。規約上の懸念があるため（`docs/policy_review_2026-10-27.md` #7・#9）。
- 外部検証：既存のグローバル Agent `yt-verify`（Web 検索のみ）を呼ぶだけ。変更しない。

## 公式 API（実装済み・模擬テストのみ・実通信は承認待ち）
`channels.list(forHandle)` で uploads プレイリスト、`playlistItems.list` で動画一覧を取る（`ytca/api.py`・通信は `crl_net/`）。
- 規約：A 方式（2026-10-27）。API の値は**保存しない**。その回の表示（件数・未着手の動画）だけに使う（`api_retention_decision.md`）。
- API データからランキング・統計・ジャンル推定は作らない（BLOCKED / NEEDS_POLICY_REVIEW）。分析の材料にもしない（一覧の網羅確認だけに使う）。
- 実装・API キー・認証は本人の承認後。それまでは「人が貼った動画 URL リスト」で運用する。
