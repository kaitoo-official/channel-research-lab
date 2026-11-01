# 公式 API の接続準備（YouTube Data API v3・未接続）

根拠：`policy_review_2026-10-27.md`・`api_review_2026-10-27.md`・`oembed_branding_review_2026-10-27.md`。

## 判定と実装
| 使い方 | 判定 | 実装 |
|---|---|---|
| @handle → チャンネル ID・アップロード一覧 ID（channels.list） | CONDITIONAL | `ytca/api.py` resolve_channel（1 unit） |
| アップロード一覧（playlistItems.list・50 件ずつ） | CONDITIONAL | `fetch_listing`：1 ページずつメモリ上だけで集める。**途中経過は保存しない**（予算で止まったら次の実行で最初から） |
| 取得する項目 | — | 動画 ID・タイトル・`contentDetails.videoPublishedAt` だけ（`fields` でも絞る）。**保存はしない** |
| 保存場所 | なし（A 方式・2026-10-27） | API の値はディスクに書かない（`api_listing.json` は廃止。古い版のファイルは起動時に `purge_api_data` で削除）。自分の記録（`videos.json`・材料・分析）は本人の操作だけで作る |
| 30 日ルール | 構造的に満たす | 保存しないので期限切れが起きない。根拠と比較は `api_retention_decision.md` |
| 一覧から消えた動画 | 削除義務 | API の値は最初から残らない。自分の記録はそのまま。完全な一覧のときだけ「最新の一覧に無い」と**表示**（**存在しないとは判断しない**・記録しない） |
| 途中で止まった一覧 | — | 「全件ではない」と表示するだけ。網羅の判定・「一覧に無い」の表示に使わない |
| 順位・統計・スコア・ジャンル推定 | BLOCKED / 要確認 | 作らない。件数は API が返す `pageInfo.totalResults` だけを表示。自分で数えた件数・自分の記録との照合は保留（`api_terms_review_2026-10-27.md`）。API の値を AI 分析の材料にしない |
| 足りない動画をページ読み取り・RSS・oEmbed で補う | 不可 / 要確認 | しない |
| 複数チャンネルの新着監視（ウォッチリスト） | CONDITIONAL | 今回は作らない（1 チャンネルのみ）。作るならチャンネルごとの表示・指標なし・合計しない |

## 通信の仕組み（`crl_net/`・`ytca/` とは別の場所）
- `crl_net/transport.py`：呼べるのは `https://www.googleapis.com/youtube/v3/channels` と `/playlistItems` だけ。使える引数も固定。
- API キーは環境変数 `CRL_YOUTUBE_API_KEY` からだけ読む。ファイル・Git・ログ・エラー・レポートに出さない（URL に key が付くので URL は記録しない）。
- 1 回 1 リクエスト。**自動の再試行なし**。403（quotaExceeded など）・5xx はそのまま報告して止まる。
- `crl_net/run_sync.py`：次の 3 つがそろわないと何も送らない
  1. 本人が作る `config/api_consent.json`（YouTube 利用規約・Google プライバシーポリシーへの同意、キーを YouTube Data API v3 に制限済み、承認者名）
  2. コマンドに `--i-approve-network`
  3. 環境変数 `CRL_YOUTUBE_API_KEY`
- 予算は 1 回 **最大 20 units**（それ以上は送信前に拒否）。足りなければ `INCOMPLETE_BUDGET`（途中までを表示するだけ・何も残さない）。次の実行は最初から取り直す。予定タスク・自動巡回には入れない。
- 4. チャンネルが手元に登録されていること（未登録なら送信前に止まる）
- 表示：既定は行を出さない（`--show none`：API の `totalResults` と取得状況だけ）。`--show all` で API の行をそのまま表示（`--limit` 件まで）。自分の記録の件数は別の欄に「YouTube のデータではない」と付けて出す。表示した一覧は保存しない。
- テストは偽の通信先だけ（実際の通信 0 回）。

## クォータ
1 日 10,000 units（既定）。全件の取得 = 1 + ceil(本数/50)。300 本で 7 units、500 本で 11 units。1 回で取り切れるのは **約 950 本まで**（20 units）。それより多いチャンネルは途中経過を保存しないため 1 回では全件にならない（その場合の方式は別途承認）。

## 本人の同意導線（API クライアントの義務・III.A）
- `docs/privacy.md`（プライバシーポリシー・Google プライバシーポリシーと YouTube 利用規約へのリンク・保存するデータ・削除方法）
- `config/api_consent.json`（本人が作成。例は下）を置くことが「利用規約に同意した」記録になる
```json
{"approved_by": "本人の名前", "approved_at": "YYYY-MM-DD",
 "accepted_youtube_terms": true, "accepted_google_privacy_policy": true,
 "key_restricted_to_youtube_data_api": true, "google_cloud_project_display_name": "Channel Research Lab"}
```
- 出典表示：一覧を表示するとき毎回「出典：YouTube Data API」と YouTube 利用規約・Google プライバシーポリシーへのリンクを出す（実装済み）。保存するレポートには API の値を入れないので出典表示も不要。ロゴは使わない。

## 公式で確認できていないこと（最初の接続で 1 チャンネルだけ確認）
アップロード一覧に限定公開・メンバー限定・予約・Shorts・ライブのアーカイブが入るか／取得件数の上限／並び順／日本語 handle の扱い。
