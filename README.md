# Channel Research Lab

YouTube チャンネル1つを研究対象にして、動画をまたいで「主張・根拠・原則・試す案」を整理する個人用の研究ツールです（Python 標準ライブラリのみ）。
**ネットには一切つながず**、人が用意した材料（動画 URL リスト・自分のメモ・確認済みの NotebookLM 要約・作者の記事・許可のある字幕）だけを扱います。

| | |
|---|---|
| 状態 | 自分の研究で使用中（2026-10 作成）。テスト 78 件 |
| 動作環境 | Python 3（外部ライブラリなし） |
| 作者 | KAITOO Works（個人開発） |

## できること
- チャンネル URL の検証・正規化（`@handle` の % エンコードと Unicode 正規化をそろえる。古い形式の URL は拒否）
- 動画一覧の管理と網羅状況（人が「これで全部」と確認するまで網羅率は UNKNOWN のまま）
- 材料の登録（5種類：自分のメモ／NotebookLM 要約／作者の記事／許可のある字幕／未視聴）。材料ごとに信頼度（basis）と範囲（coverage）を記録
- 1本ずつの分析結果（CLAIM / EVIDENCE / PRINCIPLE / TEST）を検証して保存
- 動画横断の分析：テーマの統合・2本以上で繰り返される原則・見解の分かれ目と時系列・検証候補
- Markdown レポートの出力、マニフェストによる一括取り込みと途中からの再開

## 工夫している点
- **AI の出力を信用しない検証**：分析 JSON は AI（Claude）が作るが、保存前にコードで検証する。原則は仮説のまま扱い、試す案は「変える変数を1つだけ」に限定、材料の60字以上の連続コピーは拒否
- **出典の追跡**：主張ごとに元の材料を必ず指させ、「出典で確認」「人が動画で確認」「独立した情報源で事実確認」の3つを分けて記録。どれも無い主張は要確認（needs_review）
- **作者本人のページでは事実確認にしない**：CONFIRMED には独立した一次情報か公的なデータが必要
- **安全側の既定**：ネットワークに接続する処理は別パッケージ（`crl_net/`）に分け、`--i-approve-network` を付けないと動かない。公式 API の値は保存せず、その回の表示にだけ使う
- **壊れにくい保存**：ロックで同時実行を防ぎ、ジャーナルで一括処理の途中失敗から再開できる

## 仕組み
```
人（ブラウザで普通に視聴・NotebookLM に手で登録・メモを書く）
   │  チャンネルURL / 動画URLリスト / メモ / 確認済み要約 / 作者記事 / 許可のある字幕
   ▼
ytca（Python 標準ライブラリのみ・通信なし）
   urls.py       URL の検証・正規化
   store.py      channel.json / videos.json・ロック・ジャーナル
   materials.py  5種類の材料と信頼度（basis）
   analysis.py   1本分の CLAIM / EVIDENCE / PRINCIPLE / TEST を検証
   synthesis.py  動画横断の統合
   report.py     Markdown レポート
   batch.py      マニフェストで一括取り込み・再開
crl_net（任意・承認フラグ必須）
   transport.py  YouTube Data API への通信（テストは模擬のみ）
```

## 使い方（例）
```bash
python -m ytca --root data channel-add "https://www.youtube.com/@example-channel"
python -m ytca --root data videos-add example-channel videos.tsv      # URL<TAB>タイトル<TAB>YYYY-MM-DD
python -m ytca --root data material-add example-channel <動画ID> manual_notes --file memo.md --coverage FULL
python -m ytca --root data analysis-import example-channel <動画ID> analysis.json
python -m ytca --root data synthesize example-channel
python -m ytca --root data report example-channel                     # data/channels/example-channel/report.md
```
詳しい手順は `docs/operations.md`。

## テスト
```bash
python -m unittest discover -s tests
```
`tests/fixtures/` の動画 ID・メモはすべて架空のダミーです。

## ドキュメント
| ファイル | 内容 |
|---|---|
| `docs/architecture.md` | 全体構成とデータの形 |
| `docs/operations.md` | 実際のチャンネルで使う手順 |
| `docs/api_design.md` | 公式 API を使う場合の設計（未接続） |
| `docs/api_key_setup.md` | API キーを設定する場合の手順 |
| `docs/privacy.md` | プライバシーポリシー（YouTube 利用規約 https://www.youtube.com/t/terms ／ Google プライバシーポリシー https://policies.google.com/privacy） |

> 実際の研究データ（`data/`・個別チャンネルの材料）は、権利と個人情報の扱いのため、このリポジトリには含めていません。
