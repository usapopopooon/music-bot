# music-bot

Discord 音楽 Bot — [Jockie Music](https://jockiemusic.com) の挙動・コマンド体系・UX を可能な限り再現したオープン実装。
**ギルドごとの音量設定の永続化** が独自仕様（Jockie 無料版では音量変更不可）。
詳細仕様は [SPEC.md](SPEC.md) を参照。

## 主な機能

- **Music Control Panel** — `/play` 後にチャンネルへ常駐するボタン UI（再生/停止/スキップ/音量/シャッフル/ループ/キュー/追加/検索）
- **対応音源** — YouTube / YouTube Music / Spotify / Apple Music / Deezer / SoundCloud（§3.1）
- **マルチボット** — 1 プロセス内で最大 4 個の Discord Client を並走（Jockie #1〜#4 相当）
- **ギルド単位の音量永続化** — `0–200%`、PostgreSQL に保存
- **メモリ制御** — ハードリミット (`RLIMIT_AS`) とソフトリミット監視で OOM を回避
- **slash command フォールバック** — UI 操作はすべて `/play` `/skip` `/queue` などの slash でも実行可能

## 要件

- Python 3.12
- Docker / docker-compose（ローカル一括起動用）
- Discord Bot Application 1〜4 個（各々のトークン）
- LavaSrc を Spotify / Apple Music で使う場合は各サービスの API クレデンシャル

## クイックスタート（Docker）

```bash
cp .env.example .env
# 最低限 DISCORD_TOKENS と (任意で) DEV_GUILD_ID を埋める

docker-compose up --build
```

`bot` / `lavalink` / `postgres` の 3 サービスがネットワーク内で連携起動します。
`DEV_GUILD_ID` を設定するとそのギルドに slash command が即時登録されます。
未設定の場合はグローバル登録となり、反映まで最大 1 時間かかります。

### Bot を Discord に招待する

各 Discord Application（= 各トークン）について、以下の権限・スコープで招待 URL を発行します:

- スコープ: `bot`, `applications.commands`
- 権限: `View Channels` / `Send Messages` / `Embed Links` / `Connect` / `Speak` / `Use Voice Activity`

[Discord Developer Portal](https://discord.com/developers/applications) →
対象 Application → OAuth2 → URL Generator で生成してください。

`DISCORD_TOKENS` に N 個並べる運用では、Application も N 個必要です（同一 Application を複数 Client でログインすることはできません）。

## ローカル開発（uv 直接）

Lavalink と PostgreSQL は別途立ち上げが必要です（`docker-compose up lavalink postgres` 等）。

```bash
uv sync                            # 依存解決（uv.lock を生成・更新）
uv run python -m music_bot         # Bot を起動
uv run ruff check .                # Lint
uv run ruff format --check .       # Format check
uv run mypy src                    # 型チェック
uv run pytest                      # テスト
```

> CI の `uv sync --frozen` を通すために、`uv.lock` を最初に生成してコミットしてください。

## 設定（環境変数）

詳細は [.env.example](.env.example) と [SPEC §8](SPEC.md) を参照。よく触るもの:

| 変数 | 必須 | 用途 |
|---|---|---|
| `DISCORD_TOKENS` | ✅ | カンマ区切り。1〜`MAX_BOT_INSTANCES` 個まで |
| `LAVALINK_HOST` / `LAVALINK_PASSWORD` | ✅ | Lavalink への接続情報 |
| `DATABASE_URL` | ✅ | `postgres://user:pass@host:port/db` |
| `DEV_GUILD_ID` | — | dev 環境で slash command を即時反映させたいときに |
| `MAX_BOT_INSTANCES` | — | 既定 4。同時起動する Client 数の上限 |
| `MAX_PLAYERS_PER_BOT` | — | 既定 50。1 Client あたり同時再生ギルド数の上限 |
| `MEMORY_LIMIT_MB` | — | 設定するとハードリミット (`RLIMIT_AS`) を適用 |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | — | Lavalink サービス側で参照（LavaSrc 経由 Spotify 解決） |
| `APPLE_MUSIC_TOKEN` | — | 同 Apple Music 解決 |

> `LAVALINK_PASSWORD`（Bot 側）と `LAVALINK_SERVER_PASSWORD`（Lavalink 側）は **同一値**である必要があります。Railway では Shared Variables を使って一箇所で管理してください。

## マルチボット運用

`DISCORD_TOKENS=tok_a,tok_b,tok_c,tok_d` のようにカンマ区切りで複数指定すると、その数だけ Client が並走します。

ユーザーが `/play` を実行すると、SPEC §7.7.4 の振り分けロジックで適切な Client が選ばれます:

1. 利用可能 = そのギルドで他の VC に接続中でない、または既にユーザーと同じ VC にいる
2. 既にユーザーの VC にいる Client があれば優先
3. なければ application_id 昇順で最若番

招待 URL は **Application ごと** に必要です。N 個分すべてをサーバーに招待してください。
全 Client が同ギルドで別 VC を占有している場合は「すべてのボットが使用中」とエフェメラルで通知されます。

## 操作 UI

`/play <URL or query>` で再生開始。チャンネルに **Music Control Panel** が常駐し、以後の操作はそこから行えます:

- 行 1: ⏮ Back / ⏯ Pause/Resume / ⏭ Skip / ⏹ Stop / 🔁 Loop
- 行 2: ⏪ −10s / ⏩ +10s / 🔀 Shuffle / 📜 Queue / 🔌 Leave
- 行 3: 🔉 −10 / 🎚️ <vol>% / 🔊 +10
- 行 4: ➕ Add… / 🔍 Search…

📜 Queue を押すと自分にだけ見えるエフェメラルキュービューが開きます（並べ替え・削除・ジャンプ）。

slash コマンドは UI とほぼ 1:1 で揃えてあります。詳細は [SPEC §5.6](SPEC.md) のテーブル参照。

## デプロイ（Railway）

3 サービスを **同一 Railway プロジェクトの同一 Region** に置きます:

| サービス | 種別 | Dockerfile | Public Networking |
|---|---|---|---|
| `bot` | Worker | `Dockerfile` (root) | OFF |
| `lavalink` | Worker | `lavalink/Dockerfile` | OFF（内部のみ） |
| `postgres` | Managed Database | — | — |

`railway.toml`（root）と `lavalink/railway.toml` がそれぞれのビルド設定です。
GitHub Integration で `main` ブランチを watch させると自動デプロイされます。
リソース目安・Region 選択・エグレス課金などは [SPEC §10](SPEC.md) を参照。

## CI

`.github/workflows/ci.yml` で `ruff` / `mypy` / `pytest` / Docker build を検証。
`.github/workflows/docker.yml` で `main` push 時に GHCR へ multi-arch push（任意）。

## トラブルシューティング

| 症状 | 確認ポイント |
|---|---|
| Bot がオンラインにならない / `Cannot connect to host lavalink:2333 ssl:default` | `LAVALINK_HOST` の指定ミスが大半。**Railway では `<service-name>.railway.internal`**（例: `lavalink.railway.internal`）。docker-compose ではサービス名 `lavalink` のまま。`LAVALINK_PASSWORD` と Lavalink 側 `LAVALINK_SERVER_PASSWORD` も同値に |
| `/play` でメッセージが出ない | Bot に `Send Messages` / `Embed Links` 権限があるか |
| 音が出ない | Bot に `Connect` / `Speak` / `Use Voice Activity` 権限があるか、ユーザーがボイスチャンネルにいるか |
| Spotify / Apple Music URL が解決されない | `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` / `APPLE_MUSIC_TOKEN` が Lavalink サービス側に渡っているか |
| メモリ逼迫の警告が出る | `MEMORY_SOFT_LIMIT_PERCENT` を超過。RAM プランを上げるか、`MAX_PLAYERS_PER_BOT` を絞る |
| 全 Client がすぐ落ちる | DB 接続 / Lavalink 接続が確立できないとき。Supervisor が 5 回リトライ後 exit 1（Railway が再起動） |

## 仕様・設計

すべての設計判断は [SPEC.md](SPEC.md) に集約されています。マルチボット振り分け、メモリ最適化、UI 仕様などはそちらを参照。

## ライセンス

未設定（必要なら追加してください）。
