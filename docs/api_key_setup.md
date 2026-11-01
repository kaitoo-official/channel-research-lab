# API キーの準備（本人の作業・画面の順番どおり）

> キーを作るのは本人だけ。Claude はキー作成・ログイン・入力をしない。キーをチャット・メモ・ファイルに貼らない。
> キーの準備ができるまで、Claude は実通信をしない。

使うのは **API キーだけ**（OAuth の「Google でログイン」は使わない）。本人の YouTube アカウントやチャンネルへのアクセス権は誰にも渡さない。
呼ぶのは `channels.list` と `playlistItems.list` の 2 つだけ・1 チャンネル・1 回 20 units 以内・自動の再試行なし。

## A. Google Cloud（ブラウザで本人が操作）
1. https://console.cloud.google.com を開く（いつもの Google アカウントのままでよい）
2. 画面上部の **プロジェクト選択** → **新しいプロジェクト**
   - プロジェクト名：**Channel Research Lab**（名前に YouTube / YT を入れない）
   - 組織：なし のまま → **作成** → 作ったプロジェクトに切り替える
3. **お支払い（課金）は設定しない**。「無料トライアル」や「お支払い情報を追加」の案内が出ても進まない
   （YouTube Data API v3 は割り当て制で、課金なしで使える見込み【未確認】。課金を求められたらそこで止めて相談）
4. 左メニュー **API とサービス → ライブラリ** → 「YouTube Data API v3」を検索 → **有効にする**
5. **API とサービス → 認証情報** → **＋認証情報を作成 → API キー**
   - 作成直後に出るキーは**まだコピーしない**。表示されたダイアログの **キーを編集**（または一覧の鉛筆）へ
6. キーの編集画面
   - 名前：`channel-research-lab-cli`
   - **API の制限**：「キーを制限」→ **YouTube Data API v3 だけ**にチェック（必須）
   - **アプリケーションの制限**：自宅の外向き IP が固定なら「IP アドレス」でその IP。固定でない・分からない場合は「なし」
     （その代わり：このプロジェクトは他の用途に使わない・使わない期間はキーを削除する）
   - **保存**
7. （任意・できれば）**API とサービス → 有効な API → YouTube Data API v3 → 割り当てとシステム上限**
   - 「Queries per day」を 10,000 から **200** などに下げられるか確認し、下げられたら下げる（できなければそのままで可。結果を教えてください）
8. **API キーを表示** → コピー（次の B で貼るまでクリップボード以外に置かない）

## B. Windows（本人が操作）
9. スタート →「環境変数」と入力 → **環境変数を編集**（ユーザー用）
10. ユーザー環境変数 → **新規** → 変数名 `CRL_YOUTUBE_API_KEY`、値 ＝ コピーしたキー → OK → OK
11. Claude アプリを**一度閉じて開き直す**（環境変数を読み込むため）

## C. 同意ファイル（本人が作る）
12. `YouTube Channel Analyze/config/api_consent.json` を作る（Git には入らない）。各項目を読んでから true にする：
```json
{
  "approved_by": "本人の名前",
  "approved_at": "YYYY-MM-DD",
  "google_cloud_project": "Channel Research Lab",
  "accepted_youtube_terms": true,
  "accepted_google_privacy_policy": true,
  "key_restricted_to_youtube_data_api": true
}
```
- YouTube 利用規約：https://www.youtube.com/t/terms ／ Google プライバシーポリシー：https://policies.google.com/privacy

## D. 確認接続（Claude が行う・本人の「API 確認接続をして」のあと）
13. Claude が `python -m crl_net.run_sync example-channel --i-approve-network --budget 20` を **1 回だけ**実行（既定は API が返す件数と取得状況だけ表示。一覧は保存しない）
    - 同意ファイル・`--i-approve-network`・環境変数のどれかが欠けたら送信せずに止まる
    - キーは画面・ログ・エラーに出ない（テスト済み）
14. 結果（件数・使った units・エラーの種類）を報告。失敗しても自動で再試行しない

## 使わなくなったら
Google Cloud でキーを削除 → 環境変数 `CRL_YOUTUBE_API_KEY` を削除。API の値は最初から保存していないので、消す作業はない。
