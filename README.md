# music-bot

Lavalink ベースで再生する Discord 用の音楽 Bot。詳細仕様は [SPEC.md](SPEC.md) を参照。

## 主な機能

- **Music Control Panel** — `/play` 後にチャンネルへ常駐するボタン UI（再生/停止/スキップ/音量/シャッフル/ループ/キュー/追加/検索）
- **対応音源** — YouTube / YouTube Music / Spotify / Apple Music / Deezer / SoundCloud（§3.1）
- **マルチボット** — 1 プロセス内で最大 4 個の Discord Client を並走させて同一サーバー内の複数 VC をカバー
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

`bot` / `lavalink` / `postgres` の 3 サービスがネットワーク内で連携起動する。
`DEV_GUILD_ID` を設定するとそのギルドに slash command が即時登録される。
未設定の場合はグローバル登録となり、反映まで最大 1 時間かかる。

### Bot を Discord に招待する

各 Discord Application（= 各トークン）について、以下の権限・スコープで招待 URL を発行する:

- スコープ: `bot`, `applications.commands`
- 権限: `View Channels` / `Send Messages` / `Embed Links` / `Connect` / `Speak` / `Use Voice Activity`

[Discord Developer Portal](https://discord.com/developers/applications) →
対象 Application → OAuth2 → URL Generator で生成する。

`DISCORD_TOKENS` に N 個並べる運用では、Application も N 個必要です（同一 Application を複数 Client でログインすることはできない）。

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

> CI の `uv sync --frozen` を通すために、`uv.lock` を最初に生成してコミットする。

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

> `LAVALINK_PASSWORD`（Bot 側）と `LAVALINK_SERVER_PASSWORD`（Lavalink 側）は **同一値**である必要がある。Railway では Shared Variables を使って一箇所で管理する。

## マルチボット運用

`DISCORD_TOKENS=tok_a,tok_b,tok_c,tok_d` のようにカンマ区切りで複数指定すると、その数だけ Client が並走する。

ユーザーが `/play` を実行すると、SPEC §7.7.4 の振り分けロジックで適切な Client が選ばれる:

1. 利用可能 = そのギルドで他の VC に接続中でない、または既にユーザーと同じ VC にいる
2. 既にユーザーの VC にいる Client があれば優先
3. なければ application_id 昇順で最若番

招待 URL は **Application ごと** に必要です。N 個分すべてをサーバーに招待してください。
全 Client が同ギルドで別 VC を占有している場合は「すべてのボットが使用中」とエフェメラルで通知される。

## 操作 UI

`/play <URL or query>` で再生開始。チャンネルに **Music Control Panel** が常駐し、以後の操作はそこから行える:

- 行 1: ⏮ Back / ⏯ Pause/Resume / ⏭ Skip / ⏹ Stop / 🔁 Loop
- 行 2: ⏪ −10s / ⏩ +10s / 🔀 Shuffle / 📜 Queue / 🔌 Leave
- 行 3: 🔉 −10 / 🎚️ <vol>% / 🔊 +10
- 行 4: ➕ Add… / 🔍 Search…

📜 Queue を押すと自分にだけ見えるエフェメラルキュービューが開きます（並べ替え・削除・ジャンプ）。

slash コマンドは UI とほぼ 1:1 で揃えてあります。詳細は [SPEC §5.6](SPEC.md) のテーブル参照。

## デプロイ（Railway）

3 サービスを **同一 Railway プロジェクトの同一 Region** に置く:

| サービス | 種別 | Dockerfile | Public Networking |
|---|---|---|---|
| `bot` | Worker | `Dockerfile` (root) | OFF |
| `lavalink` | Worker | `lavalink/Dockerfile` | OFF（内部のみ） |
| `postgres` | Managed Database | — | — |

`railway.toml`（root）と `lavalink/railway.toml` がそれぞれのビルド設定。
GitHub Integration で `main` ブランチを watch させると自動デプロイされる。
リソース目安・Region 選択・エグレス課金などは [SPEC §10](SPEC.md) を参照。
