# Spotify アニソン自動追加バッチ

毎日1回、「新着アニソン・BPM 135〜160」の条件に合う曲を Spotify プレイリストへ自動追加するバッチシステム。

## 機能

- `genre:anime` 検索 ＋ キーワード検索（アニソン / アニメ / 声優）の2ルートで曲を収集
- 直近7日以内のリリース曲のみ対象
- Spotify Audio Features API で BPM 135〜160 の曲に絞り込み
- 既存プレイリストの曲は重複追加しない
- 1回あたり最大20曲を追記（蓄積型）
- GitHub Actions で毎日 JST 07:00 に自動実行

## セットアップ

### 1. Spotify Developer アプリ作成

1. [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) にアクセス
2. 「Create App」でアプリを作成
3. Redirect URI に `http://localhost:8888/callback` を追加
4. `Client ID` と `Client Secret` を控える

### 2. Refresh Token の取得（ローカルで1回だけ実行）

```bash
pip install spotipy requests
SPOTIFY_CLIENT_ID=<your_id> SPOTIFY_CLIENT_SECRET=<your_secret> python scripts/get_refresh_token.py
```

ブラウザが開くので Spotify でログイン・認可する。完了後、コンソールに Refresh Token が表示される。

### 3. GitHub Secrets の登録

リポジトリの Settings → Secrets → Actions に以下を登録：

| Secret 名 | 値 |
|---|---|
| `SPOTIFY_CLIENT_ID` | Developer Dashboard の Client ID |
| `SPOTIFY_CLIENT_SECRET` | Developer Dashboard の Client Secret |
| `SPOTIFY_REFRESH_TOKEN` | 手順2で取得した Refresh Token |
| `SPOTIFY_PLAYLIST_ID` | 初回実行後に作成されたプレイリスト ID |

### 4. 初回実行

GitHub Actions の「Daily Anison Update」ワークフローを `workflow_dispatch` で手動実行する。  
実行ログに `SPOTIFY_PLAYLIST_ID=xxxxx` が表示されるので、その値を Secrets に登録する。

### 5. 自動実行の確認

翌日以降、毎日 JST 07:00 に自動実行される。

## ファイル構成

```
spotify-anison-batch/
├── .github/
│   └── workflows/
│       └── daily_update.yml      # GitHub Actions 定義
├── src/
│   └── main.py                   # メインスクリプト
├── scripts/
│   └── get_refresh_token.py      # 初回認証用（手動実行）
├── requirements.txt
└── README.md
```

## エラー対応

| エラー | 対応 |
|---|---|
| Rate limit (429) | 自動リトライ（最大3回・exponential backoff） |
| 検索結果0件 | ログ出力のみ・正常終了 |
| プレイリスト未存在 | 自動作成して処理継続 |
| 認証エラー | Actions の失敗通知でアラート |
