# Channel Research Lab

YouTube チャンネル 1 つを研究対象として、動画をまたいで主張・根拠・原則・実践案を整理する個人用ツール（Python 標準ライブラリのみ）。
**ネットには一切つながず**、人が用意した材料（動画 URL リスト・自分のメモ・確認済みの NotebookLM 要約・作者の記事・許可のある字幕）だけを扱います。

- 設計：`docs/architecture.md`／使い方：`docs/operations.md`
- テスト：`python -m unittest discover -s tests`（78 件）
- プライバシーポリシー：`docs/privacy.md`（YouTube 利用規約 https://www.youtube.com/t/terms ／ Google プライバシーポリシー https://policies.google.com/privacy）
- API 設計（未接続・本人の承認なしには通信しない）：`docs/api_design.md`／`docs/api_key_setup.md`

## 工夫している点
- **出典の追跡**：主張ごとに元の材料を必ず指させ、「出典で確認」「人が動画で確認」「独立した情報源で事実確認」の3つを分けて記録。どれも無い主張は要確認（needs_review）になる
- **AIの出力を信用しない検証**：原則は仮説のまま扱い、材料の60字以上の連続コピーを拒否する
- **安全側の既定**：ネットワークに接続する処理は別パッケージ（`crl_net`）に分け、明示の承認フラグがないと動かない
- **テストデータは架空**：`tests/fixtures/` の動画 ID・メモはすべてダミー

> 実際の研究データ（`data/`・個別チャンネルの材料）は権利と個人情報の扱いのため、このリポジトリには含めていません。
