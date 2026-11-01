# 実際のチャンネルで使う手順（例：@らんさーず）

すべて人の操作＋ローカル処理だけ。Claude は YouTube を開かない。

## 1. チャンネル登録（Claude が実行してよい）
```
python -m ytca --root data channel-add "https://www.youtube.com/@らんさーず"
```
→ key は `らんさーず`（%エンコード版の URL でも同じ key になる）

## 2. 動画 URL リストを作る（人）
- ブラウザでチャンネルの「動画」タブを普通に見て、対象の動画 URL をコピーし、`videos.tsv` に 1 行ずつ貼る（`URL<TAB>タイトル<TAB>YYYY-MM-DD`。タイトルと日付は任意）。
- 最初は 6 本程度。全件を貼り終えて「これで全部」と確認できたら：
```
python -m ytca --root data confirm-list らんさーず --by 本人 --note "動画タブを最後までスクロールして確認（○本）"
```
（確認しない限り、網羅率は UNKNOWN のまま）

## 3. 材料を用意する（人）
| 種類 | 作り方 | 登録 |
|---|---|---|
| manual_notes | 動画を見ながら自分で書いたメモ（いちばん信頼度が高い） | `material-add らんさーず <ID> manual_notes --file memo.md --coverage FULL` |
| notebooklm_summary | NotebookLM に動画を自分で追加 → 要約を読んで内容を確かめてからコピー | `... notebooklm_summary --file nb.md --human-reviewed` |
| creator_article | 作者が公開している対応記事（URL 必須。各サイトの規約に従う）。原文は保存せず要約メモにする | `... creator_article --file a.md --source-url https://... --source-title "記事タイトル" --source-date YYYY-MM-DD --linked-video-url <記事内の動画リンク> [--linked-channel-url <記事内のチャンネルリンク>]`（同じ材料で再実行すると空欄だけ埋まる。上書きはしない） |
| authorized_transcript | 保存・処理の許可が確認できた字幕だけ（誰の許可かを書く） | `... authorized_transcript --file t.txt --permission-note "..."` |
| metadata_only | 内容はまだ見ていない | `... metadata_only` |
- 第三者の字幕全文を手でコピーして保存するのは標準にしない（`NEEDS_POLICY_REVIEW`）。

## 4. 1 本ずつ分析（Claude・このプロジェクトのセッション）
「/yt-channel-analyze らんさーず <動画ID>」→ 材料を読み、`ytca-extract` が分析 JSON を作り、`analysis-import` で検証・保存。
必要なら `yt-verify`（既存）で外部検証（最大 5 件）。

## 5. 横断分析とレポート
```
python -m ytca --root data synthesize らんさーず
python -m ytca --root data report らんさーず      # data/channels/らんさーず/report.md
```

## 6. まとめて取り込む・やり直す
`tests/fixtures/pilot/manifest.json` と同じ形で manifest を作り `run-manifest`。途中で失敗したら原因のファイルを直して同じコマンドを再実行（済んだ項目は飛ばす）。

## 保留・分析不可
`status-set らんさーず <ID> ON_HOLD --reason "..."`（理由必須）。
