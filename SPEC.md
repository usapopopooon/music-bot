# Music Bot 仕様書

Discord 用の音楽 Bot。Lavalink ベースで安定再生し、ボタン UI 中心の操作と、ギルドごとの音量設定の永続化を提供する。

---

## 1. ゴール

- **UX**: slash command + ボタン UI（Music Control Panel）で完結する音楽再生体験
- **永続化**: ギルド単位の音量設定を PostgreSQL に保存し、`/volume` で自由に変更可能
- **配信形態**: Railway 上で稼働。Bot 本体・Lavalink・PostgreSQL を別サービスとして運用
- **ローカル開発**: Docker / docker-compose で全サービスを一括起動

---

## 2. 技術スタック

| 層 | 採用技術 | 理由 |
|---|---|---|
| 言語 / ランタイム | **Python 3.12** | 既存 Bot 群と統一。型ヒント + asyncio で十分な性能 |
| Discord ライブラリ | **discord.py** v2 | 現行のデファクト。slash command (app_commands) を公式サポート |
| 音声バックエンド | **Lavalink v4** | 大規模 Discord 音楽 Bot の標準。低 CPU で安定再生 |
| Lavalink クライアント | **Wavelink** v3 | discord.py 公式が推奨する Lavalink ラッパー。Lavalink v4 対応 |
| 音源プラグイン | **LavaSrc** + **YouTube Source** + **SoundCloud（Lavalink 内蔵）** | YouTube / YouTube Music の直接再生、Spotify / Apple Music / Deezer のメタ解決を経た YouTube 再生、SoundCloud の直接再生（詳細は §3.1） |
| データストア | **PostgreSQL 16** | 要件 |
| DB ドライバ | **asyncpg** | asyncio ネイティブで高速。ORM は使わず raw SQL + Pool |
| マイグレーション | 起動時に `CREATE TABLE IF NOT EXISTS` を実行する自前関数 | 単一テーブル運用のため Alembic 等は不要 |
| パッケージ管理 | **uv** (`pyproject.toml` + `uv.lock`) | 高速・再現性のあるロック生成 |
| コンテナ | Docker (multi-stage) | Railway / ローカル共通 |
| プロセス監督 | Railway / docker-compose の `restart: always` | 別途不要 |
| ソース管理 | **GitHub** | リポジトリ・Issue・PR・Actions を統合 |
| CI | **GitHub Actions** | lint / typecheck / test / docker build を PR ごとに実行 |
| Lint / Format | **Ruff** (lint + format) | 単一ツールで完結、CI 高速 |
| 型チェック | **mypy** (strict) | 型安全 |
| テスト | **pytest** + **pytest-asyncio** | Python 標準的構成 |

### なぜ Lavalink か
- discord.py 単体では音声送信は可能だが YouTube 抽出は yt-dlp 等に頼ることになり、長時間運用でレートリミットや BOT 検出に弱い。
- Lavaplayer (Lavalink の音声エンジン) は大規模 Discord 音楽 Bot で広く採用されており、再生品質と安定性の実績が豊富。
- Wavelink を使えば Python 側のコードは薄く保てる。

---

## 3. アーキテクチャ

```
┌──────────────────┐    gateway × N   ┌──────────────────────────┐
│ Discord Voice    │◀────────────────▶│  App (1 Python process)  │
│   Gateway        │                   │  ┌────────────────────┐ │
└──────────────────┘                   │  │ discord.py Client #1│ │
        ▲                              │  │ discord.py Client #2│ │
        │ UDP (RTP) × N (Voice)        │  │ ...           #N    │ │
        │                              │  └────────────────────┘ │
        │                              │  Wavelink Pool (shared) │
        │                              └─────┬────────────────────┘
        │                                    │ WebSocket / REST
        │                                    ▼
        │                            ┌──────────────────┐
        └───────────────────────────▶│  Lavalink v4     │
                                     │  + LavaSrc       │
                                     │  + youtube-source│
                                     └──────────────────┘

┌──────────────────────────┐
│  App (Python)            │ ──── TCP/SSL ────▶  PostgreSQL
│  asyncpg Pool (shared)   │                   (guild_bot_settings)
└──────────────────────────┘
```

- 1 プロセスに `discord.Client` を **N 個** 並走させ、Wavelink / asyncpg / Lavalink 接続は全 Client で共有する（§7.7）。
- 各 Client は独立した Discord Application（別トークン・別 application_id・別名・別アバター）を持つ。

Railway 上のサービス構成:
1. `bot` — Python 3.12 コンテナ 1 つ。N 個の Discord Client を内部で並走。
2. `lavalink` — Java 17 / Lavalink v4 コンテナ。Bot からのみアクセス可能な内部ネットワーク。
3. `postgres` — Railway の PostgreSQL アドオン。

### 3.1 対応音源

Phase 1 で再生可能な音源と再生経路を正式に定義する。`lavalink/application.yml` の `lavalink.sources` および `plugins.lavasrc.sources` の有効化フラグはこの表に従う。

#### 3.1.1 有効化する音源

| 音源 | 取り込み元 | 再生経路 | URL 例 | 認証 |
|---|---|---|---|---|
| **YouTube** | `youtube-source` プラグイン | 直接ストリーミング | `https://www.youtube.com/watch?v=...`<br>`https://youtu.be/...`<br>`https://www.youtube.com/playlist?list=...` | 不要 |
| **YouTube Music** | `youtube-source` プラグイン（同一ハンドラ） | 直接ストリーミング | `https://music.youtube.com/watch?v=...`<br>`https://music.youtube.com/playlist?list=...`<br>`https://music.youtube.com/browse/...` | 不要 |
| **Spotify** | LavaSrc（曲名・アーティスト解決） | **YouTube 経由**で再生 | `https://open.spotify.com/track/...`<br>`https://open.spotify.com/album/...`<br>`https://open.spotify.com/playlist/...` | `SPOTIFY_CLIENT_ID` + `SPOTIFY_CLIENT_SECRET`（§8.2） |
| **Apple Music** | LavaSrc（曲名・アーティスト解決） | **YouTube 経由**で再生 | `https://music.apple.com/.../song/...`<br>`https://music.apple.com/.../album/...`<br>`https://music.apple.com/.../playlist/...` | `APPLE_MUSIC_TOKEN`（§8.2） |
| **Deezer** | LavaSrc（曲名・アーティスト解決） | **YouTube 経由**で再生 | `https://www.deezer.com/track/...`<br>`https://www.deezer.com/album/...`<br>`https://www.deezer.com/playlist/...` | `DEEZER_ARL`（任意・既定では無効。LavaSrc 4.8+ で arl Cookie が必須化されたため §3.1.5 参照） |
| **SoundCloud** | Lavalink v4 内蔵 | 直接ストリーミング | `https://soundcloud.com/<user>/<track>`<br>`https://soundcloud.com/<user>/sets/<playlist>` | 不要 |
| **検索クエリ** | YouTube 検索（`/search` または `/play <非 URL>`） | YouTube 経由 | 自由文（例: `Mr. Children innocent world`） | 不要 |

#### 3.1.2 無効化する音源

メモリ節約・保守性・セキュリティの観点で `enabled: false` とする。

| 音源 | 無効化理由 |
|---|---|
| Twitch | ライブ配信用途、本 Bot のスコープ外 |
| Bandcamp | 利用率が低い |
| Vimeo | 利用率が低い |
| HTTP(S) 直リンク（`.mp3` / `.ogg` 等の任意 URL） | 任意 URL の取得は帯域・タイムアウト・セキュリティ懸念がある |
| Local files | サーバーローカルファイルの再生はサポート外 |

#### 3.1.3 URL マッチングの優先順位

`/play <query>` または ➕ Add モーダルで URL が渡された場合、Lavalink が先頭から順に各ソースの正規表現に当てる。優先順位:

1. `youtube-source`（`youtube.com` / `youtu.be` / `music.youtube.com` を一括処理）
2. LavaSrc Spotify（`open.spotify.com`）
3. LavaSrc Apple Music（`music.apple.com`）
4. LavaSrc Deezer（`deezer.com`）
5. SoundCloud（`soundcloud.com`）
6. いずれの URL パターンにもマッチしなければ → **YouTube 検索クエリ**として扱う

#### 3.1.4 Spotify / Apple Music / Deezer の再生方式

- LavaSrc は曲名・アーティスト名・サムネイル等のメタデータ解決のみを行い、実際の音声ストリームは取得しない。
- 解決されたメタデータを使って **YouTube で同曲を検索** し、最初の検索結果を再生する。
- そのため:
  - 再生されるのは YouTube 上に存在する版であり、原音とは限らない（公式 MV / カバー / ライブ版など）。
  - YouTube に該当曲が見当たらない場合は再生失敗（§7.5）。
  - プレイリスト URL は中の全曲をそれぞれ YouTube 解決する。100 曲超のプレイリストでは時間がかかるため、解決中はエフェメラルで「Loading playlist… (12/50)」のような進捗を返す。
- LavaSrc を使う Lavalink ベースの音楽 Bot で広く採用されている方式。

#### 3.1.5 認証情報の入手元

- **Spotify**: <https://developer.spotify.com/dashboard> で App を作成し Client ID / Client Secret を取得。
- **Apple Music**: 開発者アカウントから MusicKit private key を生成し、JWT トークン（最大 6 ヶ月有効）を発行。期限切れ前にローテーション。
- **Deezer**: LavaSrc 4.8 以降は `arl` Cookie が必須（無いと LavaSrc プラグインが起動失敗するため Lavalink ごと落ちる）。本仕様の既定構成では Deezer は **無効化**しており、必要な場合のみ以下の手順で有効化する:
  1. Deezer に Web ログインしてブラウザの開発者ツール → Application → Cookies → `deezer.com` ドメインの `arl` の値をコピー
  2. lavalink サービスの env var に `DEEZER_ARL=<その値>` を追加
  3. `lavalink/application.yml` で `plugins.lavasrc.sources.deezer: true` に変更し、`plugins.lavasrc.deezer.arl: "${DEEZER_ARL}"` のコメントを外す
  - arl は数ヶ月でローテーションされるので運用上手間がかかる。Spotify / Apple Music で代替できることが多いので Phase 1 では既定 OFF とした。
- 上記いずれも未設定の場合、その音源のみ無効化されて他の音源は引き続き利用可能。

---

## 4. データモデル

PostgreSQL に保存するのは「Bot 再起動後も保持したい設定値」のみ。キュー・現在再生位置などはメモリのみ（再起動で消える）。

### 4.1 `guild_bot_settings`

ボットごと・ギルドごとに独立した設定値を保持する（§7.7 マルチボット）。

| カラム | 型 | 制約 | 説明 |
|---|---|---|---|
| `guild_id` | `BIGINT` | composite PK | Discord ギルド ID |
| `bot_id` | `BIGINT` | composite PK | この行の対象となるボットの application_id |
| `volume` | `SMALLINT` | `NOT NULL DEFAULT 1`, `CHECK (volume BETWEEN 0 AND 100)` | 0–100 (%) の表示値。Lavalink へ渡す値は `(value+1)/2` で 0–50 に変換（増幅させない） |
| `updated_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` | 監査用 |

```sql
CREATE TABLE IF NOT EXISTS guild_bot_settings (
  guild_id   BIGINT NOT NULL,
  bot_id     BIGINT NOT NULL,
  volume     SMALLINT NOT NULL DEFAULT 1 CHECK (volume BETWEEN 0 AND 100),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (guild_id, bot_id)
);
```

### 4.2 アクセスパターン

- **読み取り**: ボットがボイスチャンネルに参加した直後に 1 回。`MusicPlayer.set_display_volume()` 経由で Lavalink に反映（行が無ければ既定値 `1` を適用）。
- **書き込み**: 音量変更（🔉 / 🔊 / 🎚️ / `/volume`）のたびに以下を実行:

```sql
INSERT INTO guild_bot_settings (guild_id, bot_id, volume)
VALUES ($1, $2, $3)
ON CONFLICT (guild_id, bot_id)
DO UPDATE SET volume = EXCLUDED.volume, updated_at = now();
```

- 行が無いときは「メモリ上は既定値 `1` として扱い、変更時に初めて INSERT」とする（書き込み回数を最小化）。
- 1 ギルドに複数ボットが招待されている場合、ボットごとに独立した音量を持つ。

---

## 5. 操作仕様

### 5.1 設計方針: UI 優先

- **すべての操作をボタン / select menu / モーダルで完結**させる。
- ユーザーがタイプする slash command は再生開始時の **`/play`** を中心に最小限とし、それ以外はパワーユーザー向けフォールバックとして残す（§5.6）。
- `/play` で再生が始まると、コマンド実行チャンネルに **Music Control Panel** が常駐し、以後の全操作はそこから行える（§5.2）。
- 純テキスト・数値入力が必要な操作（曲追加・検索・音量数値・シーク時刻）は **モーダル** で受ける（§5.3）。
- キューに対する操作（削除・並べ替え・指定再生）は **ephemeral な Queue ビュー**（§5.4）で完結させ、公開チャンネルを汚さない。

### 5.2 Music Control Panel（常駐 UI）

`/play` 直後にチャンネルへ投稿される単一メッセージ。再生が続く間 **同じメッセージを編集して更新**し続ける。切断時は自動的にボタンを無効化、または削除。

#### 5.2.1 Embed 部

```
┌────────────────────────────────────────┐
│ 🎵 Now playing                          │
│ [曲タイトル](URL)                        │
│ by アーティスト名                        │
│                                        │
│ ▬▬▬▬▬●▬▬▬▬▬▬▬  01:23 / 03:45         │
│                                        │
│ Up next: 次曲タイトル — アーティスト       │
│ (+ あと N 曲, 残り MM:SS)                │
│                                        │
│ Requested by @user                      │
└────────────────────────────────────────┘
```

- サムネイルはトラック artwork。
- プログレスバーは 20 文字、現在位置を `●` で表示（プレーンテキスト Unicode 文字を使い、Discord 上で行高がズレないようにする）。
- ライブ配信は時刻欄を `🔴 LIVE` に置換。
- Up next が無いときは「キューは空です」と表示。
- Loop / Shuffle / Volume の現在値は **ボタンラベル側**にのみ表示（Embed 本文には載せない。情報の一元化）。
- 5 秒間隔で position を更新（Discord の編集レート制限内）。
- 状態変化（pause / resume / skip / loop / shuffle / volume / 曲変更）は即時更新。

#### 5.2.2 ボタン配置

最大 4 行。`custom_id` は全て `mb:<action>` プレフィックス（例: `mb:skip`, `mb:vol_up`, `mb:loop_cycle`）。

| 行 | ボタン | 動作 |
|---|---|---|
| 1 トランスポート | ⏮ Back / ⏯ Pause/Resume / ⏭ Skip / ⏹ Stop / 🔁 Loop: <mode> | Pause/Resume はトグル。Loop は off→track→queue を循環し、ラベルが現在モードを表示 |
| 2 移動・キュー | ⏪ −10s / ⏩ +10s / 🔀 Shuffle: <on/off> / 📜 Queue / 🔌 Leave | Queue は ephemeral な Queue ビューを開く（§5.4）。Leave は切断のみ（キュー保持）。Shuffle はラベルに現在状態を表示 |
| 3 音量 | 🔉 −10 / 🎚️ 1% / 🔊 +10 | 中央のボタンはラベルが現在の音量（表示値）を表示し、押下で「音量設定モーダル」を開く |
| 4 追加 | ➕ Add… / 🔍 Search… | それぞれ「追加モーダル」「検索モーダル」を開く |

- ⏯ は再生中なら ⏸ に、停止中は ▶ にラベル / アイコンを切り替え。
- ⏮ Back は **履歴が空**（直前に再生し終えた曲が無い）の場合は disabled。
- ⏪ −10s は再生位置が 10 秒未満の時は 0 へクランプ、⏩ +10s は曲長を超える時はスキップ扱い。ライブ配信時は両方 disabled。
- 音量増減は ±10 単位、0–100 でクランプ。変更は **即 DB 保存**（§5.5）。
- ボタン押下のフィードバックは原則 **Control Panel の Embed 更新** のみで完結（チャットを汚さない）。確認が必要な操作（例: 切断）はエフェメラル `Disconnected.` を返す。
- 同じボイスチャンネルにいないユーザーが押した場合はエフェメラルで `🛑 Join the same voice channel first.` を返す（§7.2）。

### 5.3 モーダル

ボタンから開く。Discord のモーダル制限（最大 5 入力 / 1 モーダル）に収まる。

| 起動元 | フィールド | 制約 | 結果 |
|---|---|---|---|
| ➕ Add | `URL or query` (必須・短文) | 1–256 文字 | キュー末尾に追加。プレイリスト URL は全曲を一括追加。エフェメラル `Added: タイトル` を返す |
| 🔍 Search | `query` (必須) | 1–128 文字 | YouTube 検索を実行し、エフェメラルメッセージで select menu (最大 5 件) を返す。選択で 1 曲追加 |
| 🎚️ Volume | `level` (必須, 0–100) | 整数 | 即時反映 + DB 保存（§5.5） |

> シーク（任意位置）は誤操作と低頻度を考慮して **Control Panel には置かず**、`/seek` のみ提供。Control Panel の ⏪ / ⏩ で大半は事足りる。

### 5.4 Queue ビュー（📜 Queue ボタン）

📜 押下で **エフェメラル**（実行者にだけ見える）な Queue ビューを開く。最大 5 行構成。

```
[Embed: 1 ページ 10 件のキュー（タイトル / アーティスト / 長さ / 追加者）]

Row 1: <select menu>「曲を選択」  (現ページの 10 件のみを options として提示)
Row 2: [⬆ 1つ上へ] [⬇ 1つ下へ] [🔝 先頭へ] [🗑 削除] [▶ ジャンプ]
Row 3: [◀ Prev page] [▶ Next page] [🔀 Shuffle] [🧹 Clear All] [✖ 閉じる]
```

- select menu には **現ページの 10 件のみ**を options として並べる（Discord 上限 25 / メニューだが、ページ境界を跨ぐ選択は混乱の元になるため意図的に絞る）。
- 隣接ページの曲を操作したいときは ◀ / ▶ でページを切り替えてから選び直す。
- select menu で 1 曲選択 → Row 2 のボタンで操作:
  - ⬆ ⬇: 1 つ移動（端では disabled）。任意位置への移動は `/move` のみ。
  - 🔝 先頭へ: 選択曲をキュー先頭に移動（再生中曲の次へ。`/playnext` の既存曲版）。
  - 🗑 削除: 選択曲を削除（`/remove` 相当）。
  - ▶ ジャンプ: 選択位置までスキップ。途中の曲は履歴に積まれない（`/jump` 相当）。`/skipto` は途中曲を順次再生扱いとする差分があり、UI からは提供しない（slash のみ）。
- 「🧹 Clear All」は確認モーダル（テキスト一致）を介さず、押下時に Control Panel 側へ「キューを空にしました」のフッタを 5 秒表示。
- 60 秒無操作でボタンを無効化（再表示するには再度 📜 を押す）。
- キュー総数 > 250 ページ × 10 = 2500 件は `MAX_QUEUE_SIZE`（既定 500）でブロックされるため発生しない。

### 5.5 音量（永続化・独自仕様）

- **表示値の範囲**: `0`–`100`（既定値 `1`）。UI / DB / slash command すべてこの値を扱う。
- **内部 Lavalink 値への変換**: `(display+1) // 2` で `0`–`50` にマップ。表示 `100` でも Lavalink 50（= 原音の半分の振幅）にしかならず、Lavalink の volume フィルタが**増幅しない**ため、クリッピング由来の聴覚ダメージリスクを物理的に排除する設計。
  - 表示 `0` → Lavalink `0`（無音）
  - 表示 `1` → Lavalink `1`（既定値・最小可聴）
  - 表示 `100` → Lavalink `50`（原音の半分）
- 操作 UI:
  - 🔉 −10 / 🔊 +10（Control Panel 行 3）
  - 🎚️ <現在値>% 押下 → 数値モーダル
  - `/volume [level]`（slash フォールバック）
- **すべての音量変更で同一トランザクション内に DB 保存**（`INSERT ... ON CONFLICT DO UPDATE`）。
- 反映先: `MusicPlayer.set_display_volume()` 経由で Lavalink Player の音量フィルタ。次回 `/play` 時はこの値で起動。

### 5.6 slash command フォールバック

UI から実行できる全操作を slash command でも提供する（モバイルの誤タップ対策・自動化用途）。

| 操作 | 主 UI | slash command | 備考 |
|---|---|---|---|
| 再生開始 / 追加 | `/play` のみ（最初の入口） | `/play <query>` | Panel 不在時の唯一の入口 |
| 次に追加 | Queue ビュー → 🔝（既存曲）/ ➕ Add → 🔝 | `/playnext <query>` | 新曲を次に追加するには「➕ Add で末尾に追加→Queue ビューで 🔝」の 2 ステップ |
| 即時再生 | （UI 無し） | `/playnow <query>` | 新曲を即時再生する UI 一発操作は無い。Add → 🔝 → ⏭ の 3 ステップで等価 |
| 検索 | 🔍 Search ボタン | `/search <query>` | |
| 一時停止 / 再開 | ⏯ ボタン | `/pause` `/resume` | |
| スキップ | ⏭ ボタン | `/skip` | |
| 指定位置までスキップ | （UI 無し、`/jump` で代替推奨） | `/skipto <position>` | 途中の曲を「再生済み」として扱う点が `/jump` と異なる。UI からは差が分かりづらいため slash のみ |
| ジャンプ | Queue ビュー → ▶ | `/jump <position>` | 途中の曲は履歴に積まれない |
| 戻る | ⏮ ボタン（履歴ありのみ） | `/back` | 履歴空時は disabled |
| 頭出し | （UI 無し） | `/replay` | |
| シーク（任意） | （UI 無し） | `/seek <time>` | ⏪ / ⏩ で代替 |
| 早送り / 巻き戻し | ⏪ / ⏩ ボタン | `/forward [s]` / `/rewind [s]` | |
| 停止 | ⏹ ボタン | `/stop` | |
| キュー一覧 | 📜 Queue ボタン | `/queue [page]` | |
| 現在再生 | Control Panel そのもの | `/nowplaying` | |
| 全消去 | Queue ビュー → 🧹 | `/clear` | |
| 1 曲削除 | Queue ビュー → 🗑 | `/remove <position>` | |
| 入れ替え | Queue ビュー → ⬆ ⬇（隣接のみ） | `/move <from> <to>` | 任意位置への移動は slash のみ |
| シャッフル | 🔀 ボタン | `/shuffle` | |
| ループ | 🔁 ボタン | `/loop <mode>` | |
| DM 取得 | （UI 無し） | `/grab` | |
| ボイス参加 | （Panel が無い時のみ） | `/join` | |
| 切断 | 🔌 Leave ボタン | `/disconnect` | |
| 音量 | 🔉 / 🔊 / 🎚️ ボタン | `/volume [level]` | |

### 5.7 フィルタ（Phase 2）

Phase 2 で Control Panel に「フィルタ」行を追加し、各フィルタを on/off するボタンを並べる予定。Lavalink の filter API を使用する。

| 候補 | 説明 |
|---|---|
| `bassboost` | 低音強調 |
| `nightcore` | 速度・ピッチ上昇 |
| `vaporwave` | 速度・ピッチ低下 |
| `8d` | 定位回転 |
| `karaoke` | ボーカル抑制 |
| `tremolo` / `vibrato` | 揺らぎ |

---

## 6. ビジュアル仕様

### 6.1 共通色

| 状態 | 色 | 用途 |
|---|---|---|
| 通常 / 再生中 | `#5865F2` (Discord Blurple) | Control Panel の標準色 |
| 一時停止 | `#FAA61A` | Embed 左帯を黄色に |
| 切断 / 終了 | `#4F545C` | Panel 終了状態 |
| エラー | `#ED4245` | 読み込み失敗・権限エラー |
| 成功 (エフェメラル) | `#57F287` | 「Added: …」「Saved volume: …」など |

### 6.2 Control Panel の更新ポリシー

- **編集レート**: 各 Panel あたり 5 秒に 1 回まで（progress 更新）。状態変化は即時。
- **マルチボットで同チャンネルに複数 Panel がある場合**: Discord のメッセージ編集レートリミットは「ボット × チャンネル」単位で適用されるため、各 Panel は独立してリミット内（5 編集 / 5 秒）に収まる。複数ボットが同チャンネルに居ても累積制限はかからない。
- **公開メッセージは Control Panel のみ**を維持し、ボタン操作の確認は全て **エフェメラル**で実行者に返す。
- エラー（読み込み失敗等）はエフェメラルで詳細を返し、Control Panel のフッターに「⚠️ Last track failed to load」を 10 秒だけ表示。
- セッション終了時（`/stop` または自動切断）は Embed 色をグレーに切り替え、ボタンを全て disabled に。

### 6.3 アクセシビリティ

- すべてのボタンに **絵文字 + テキストラベル** を併記（絵文字非対応環境でも判別可能）。
- 色だけで状態を伝えない（Loop モードはラベル文字列で表示）。
- スクリーンリーダー向けに、ボタンのアクセシブルラベル（Discord 既定の `label`）を曖昧でない動詞句にする（`Pause` / `Resume` / `Skip to next track` 等）。

---

## 7. 動作仕様の細部

### 7.1 ボイス参加 / 自動切断

- `/play` 等の再生系コマンドは「実行者がボイスチャンネルにいること」を必須とする。
- **VC から最後の人間が抜けた瞬間に自動切断**（再生中・キューの中身に関わらず）。
- `/stayalone` でセッション中だけ自動切断を無効化できる（再起動・再接続で OFF に戻る）。トグル形式で、OFF に戻した時点で VC が無人なら即時切断する。
- 24/7 モード（再起動を跨いだ VC 常駐）は Phase 1 では非対応（`/stayalone` は揮発フラグ）。

### 7.2 権限チェック

- 実行者が再生 Bot と同じボイスチャンネルにいる場合のみ操作可。
- ただし「ボイスチャンネルに人間が誰もいない」場合は誰でも操作可（メンテ用途）。

### 7.3 Loop 挙動

- `track`: 同一トラックを終わるたびに頭出し。
- `queue`: キュー末尾まで再生したらキュー先頭から再開。
- `off`: 再生終了でキューから消費。

### 7.4 履歴 (`/back`)

- 直近に再生し終えた 1 曲のみ保持（メモリ）。再起動で消失。
- スコープは **`(bot_id, guild_id)` 単位** — マルチボット運用では各 Client が各ギルドにつき独立した履歴を持つ。
- `bot_id` がそのギルドから切断された時点で履歴はクリアされる。

### 7.5 エラー時の挙動

- トラック読み込み失敗: 該当曲をスキップし「⚠️ Failed to load: タイトル」を投稿、次曲へ。
- **Lavalink 起動時の接続失敗（初回接続）**: Bot プロセスは Discord ゲートウェイへ接続せず、Lavalink ノードへ 30 秒間隔で 5 回までリトライ。全失敗で起動失敗（exit 1）→ Railway が再起動。Discord 上には「オンライン」と表示されない（無音失敗を避ける）。
- **Lavalink ノード切断（運用中）**: 既存の Player を維持したまま、再接続を 5 回までリトライ（指数バックオフ 2/4/8/16/32 秒）。再接続成功で再生再開、5 回失敗で全 Player を破棄しエフェメラルで通知。Bot プロセスは継続。
- **Client supervisor のリトライ**（§7.7.3）と上記 Lavalink リトライは独立したループで動作する。
- DB 接続失敗: 起動失敗で終了 (Railway が再起動)。

### 7.6 ロギング

#### 7.6.1 Bot プロセス（Python）

- 標準ライブラリ `logging` モジュールベース。レベル: `INFO` / `WARNING` / `ERROR` / `DEBUG`。
- フォーマット: `[ISO8601] [LEVEL] [bot_name] [scope] message`（マルチボット運用のため `bot_name` を必ず含める）。
- `LOG_LEVEL` 環境変数でレベル切替。
- 機密情報（トークン・DB URL）は出力しない。

#### 7.6.2 Lavalink プロセス（Java / logback）

- Lavalink は `application.yml` の `logging.level.root: INFO` で制御（`logback` ベース、Bot 側の `logging` とは独立）。
- 既定で標準出力に出すため Railway のログタブにそのまま流れる。
- LavaSrc 等のプラグインも同じ logback 設定を共有。
- 障害切り分けのため `logging.level.lavalink: INFO`、再接続調査時のみ `DEBUG` を選ぶ。

### 7.7 マルチボット（1 プロセス N Client）

同一サーバー内の複数ボイスチャンネルで同時に再生できるよう、**1 つの Python プロセス内で複数の `discord.Client` を並走**させる方式を採る。

#### 7.7.1 起動モデル

- 環境変数 `DISCORD_TOKENS` にカンマ区切りで N 個のトークンを与える。
- 起動時に各トークンで `discord.Client` を生成し、`asyncio.gather(*[c.start(token) for c, token in zip(clients, tokens)])` で並走。
- 起動時のバリデーション（いずれか満たさなければ起動失敗 exit 1）:
  - **下限**: `len(DISCORD_TOKENS) >= 1`
  - **上限**: `len(DISCORD_TOKENS) <= MAX_BOT_INSTANCES`（既定 4）
  - **重複禁止**: 同一トークンが複数指定されていないこと（同一 application_id の二重ログインは Discord 側で拒否される）
  - **形式**: 各トークンが空文字でないこと（簡易チェック、無効トークンの実検出はログイン時に行う）
- 各 Client は `application_id` をログイン後に取得し、それが §4 `bot_id` として使われる。

#### 7.7.2 共有 / 独立リソース

| リソース | 共有 | 説明 |
|---|---|---|
| Wavelink Pool（Lavalink 接続） | ✅ 共有 | 1 Lavalink ノードに対して全 Client が接続。Player は Client ごとに独立 |
| asyncpg Pool（DB 接続） | ✅ 共有 | コネクション総数 = `DB_POOL_SIZE` |
| ロガー | ✅ 共有設定 | レコードに `bot_name` を埋めて区別 |
| メモリ上限（`resource.setrlimit`） | ✅ 共有 | プロセス全体に対して 1 つ（§7.8） |
| Discord トークン / application_id / プレゼンス / アバター | ❌ 独立 | ボットごと |
| Player（ボイス接続・キュー・現在再生・履歴） | ❌ 独立 | 同一ギルドでも Client ごとに別 Player |
| Control Panel メッセージ | ❌ 独立 | ボットごとに別メッセージ。同チャンネルに #1 と #2 の Panel が並ぶ |
| slash command 登録 | ❌ 独立 | 各 application_id に対して個別に `tree.sync()` |
| 音量設定（DB） | ❌ 独立 | `(guild_id, bot_id)` キー |

#### 7.7.3 障害分離

- 1 Client が `discord.errors.ConnectionClosed` などで死んだ場合、その `client.start` タスクのみが例外で抜ける。
- スーパーバイザは死んだ Client を最大 5 回まで指数バックオフで再起動（§7.5 と同じ方針）。
- 5 回連続で失敗した Client は disable 状態で残り、他の Client は継続。プロセスは落とさない。
- ただし **全 Client が同時に disable 状態**になった場合のみ exit code 1 で終了し、Railway による再起動を促す。

#### 7.7.4 ユーザー視点の挙動 / 自動振り分け

- 招待 URL はボットごとに別。`README.md` に N 個分を併記する。

##### 「Client X はギルド G で利用可能」の定義

- ✅ 利用可能: X が G のいずれの voice channel にも接続していない、**または** X が G で実行者と **同じ** voice channel に接続中
- ❌ 利用不可: X が G の **別の** voice channel に接続中

「他ギルドでの再生」は利用可能性に影響しない（Player はギルド単位で独立）。

##### 振り分けアルゴリズム（`/play` 受信時）

1. 実行者の voice channel `vc` を取得。`vc` が無ければ「ボイスチャンネルに参加してください」エフェメラルを返す。
2. ギルド G に対する **per-guild の `asyncio.Lock`** を取得（同時 `/play` の競合防止）。
3. 上の定義で「G で利用可能」な Client の集合を作る。
4. その集合の中から:
   - 既に `vc` に居る Client があれば最優先で選択
   - 居なければ **application_id 昇順**で最若番を選択
5. 集合が空なら「すべてのボットがこのギルドの別ボイスチャンネルで使用中です（招待されていない場合は招待 URL も提示）」をエフェメラルで返す。
6. Lock を解放。

##### その他

- 既に Control Panel が表示されているチャンネルとは別チャンネルから `/play` した場合、振り分けで別 Client が選ばれる可能性がある（同一ギルド内の別 VC で複数 Client が同時稼働する）。
- 全 Client が招待されていないギルドでは応答できない。`README.md` の招待 URL に従って事前に招待してもらう。

### 7.8 メモリ上限・リソース制御

メモリ消費はマルチボット化と Player 数に応じて線形に伸びるため、**設定可能な上限**を設ける。

#### 7.8.1 ハードリミット

- 環境変数 `MEMORY_LIMIT_MB`（任意, 既定なし）。
- 設定すると起動時に `resource.setrlimit(resource.RLIMIT_AS, (n*1024*1024, n*1024*1024))` を呼びプロセスの **仮想メモリ (VSZ)** を制限。実 RSS 制限ではなく、ピーク到達前に Python 側で `MemoryError` を投げて落ちる仕組み。
- 超過時は `MemoryError`（Python 例外）または OS による SIGKILL。Railway が自動再起動。
- **Railway の cgroup 制限との関係**: Railway はサービスの RAM 上限を **cgroup memory.max** で強制し、超過時は OOM Killer がコンテナを SIGKILL する（Python 側に通知無し）。`RLIMIT_AS` はこれより **約 10% 低く**設定することで、cgroup OOM の前に `MemoryError` で graceful に落ちるようにする補助的役割。両者は競合せず併用可。
- **プラットフォーム別挙動**:
  - Linux（Railway / Docker 本番）: 想定通り強制。
  - macOS: `RLIMIT_AS` が機能しないため、警告ログのみ出してスキップ。
  - Windows / WSL2: ローカル開発のみサポート。`resource` モジュールが Windows ネイティブで動かないため、`platform.system() == 'Windows'` を検出したら同様にスキップ。WSL2 上では Linux 同様に動作する。

#### 7.8.2 ソフトリミット

- 環境変数 `MEMORY_SOFT_LIMIT_PERCENT`（既定 `90`）。
- 起動時に `psutil` で 30 秒ごとに RSS を測定するバックグラウンドタスクを開始。
- ソフトリミット超過中は:
  - 新規 Player 作成（`/play` の最初の 1 曲）を拒否し、エフェメラルで「⚠️ 一時的に再生を停止中です（メモリ逼迫）」を返す。
  - 既存の再生は継続。
  - WARN ログを 60 秒に 1 回出す。
  - Control Panel フッターに `⚠️ Memory pressure` を表示。
- ソフトリミットを下回ったら自動復帰。

#### 7.8.3 上限ガード

- `MAX_BOT_INSTANCES`（既定 `4`）— 同時起動可能な Client 数の上限。
- `MAX_PLAYERS_PER_BOT`（既定 `50`）— 1 Client あたりの同時再生ギルド数の上限。
- 超過時の応答: 新規 Player 作成を拒否し、エフェメラルで `🛑 このボットは同時再生数の上限 (50) に達しました。他のボットを試すか、別のギルドで再生中の曲が終わるまでお待ちください。` を返す。`/play` 実行者は §7.7.4 の振り分けによって自動的に別 Client が試される（全 Client 上限到達時のみメッセージが表示される）。

#### 7.8.4 メモリ消費の目安（§7.9 の最適化を実施した状態）

| 構成要素 | 概算 RSS |
|---|---|
| Python プロセス基礎（最小化後） | 50 MB |
| `discord.Client` 1 個（最小 intents・キャッシュ無効・接続済み空稼働） | +12 MB |
| アクティブな Player 1 個（曲再生中・キュー 10 件・`__slots__`） | +2 MB |
| Wavelink Pool（共有・常時、Lavalink への WebSocket 接続を含む） | +18 MB |
| asyncpg Pool（4 接続） | +6 MB |

> **注記**: 以下の例で `G` は **同時再生中の Player 数（= active player）** を指す。Bot が招待されているギルド総数ではない。Player を持たないアイドルギルド（メンバーが居ない・再生していない）はメモリを消費しない（§7.9.4）。

**例**:
- N=1, G=10 (1 Client が 10 ギルドで同時再生) → 50 + 12 + 10×2 + 18 + 6 = **106 MB**
- N=2, G=10×2 (各 Client が 10 ギルドで再生) → 50 + 2×12 + 20×2 + 18 + 6 = **138 MB**
- N=4, G=10×4 → 50 + 4×12 + 40×2 + 18 + 6 = **202 MB**
- N=4, G=50×4（最大想定。`MAX_PLAYERS_PER_BOT=50` × 4 Client） → 50 + 4×12 + 200×2 + 18 + 6 = **522 MB**

#### 7.8.5 Lavalink 側のメモリ

- Lavalink は別プロセス（別 Railway サービス）。本 Bot のメモリ上限とは独立。
- JVM 引数: `-Xms64m -Xmx${LAVALINK_MAX_HEAP_MB}m -XX:+UseSerialGC`
  - SerialGC は小規模常駐用途で G1GC より RSS が小さい。
  - `Xms` を低く設定することで起動直後のヒープを抑える。
  - `-Xmx` を明示的に指定する場合、`-XX:MaxRAMPercentage` は無視されるので併記しない。
- **`LAVALINK_MAX_HEAP_MB` の既定値は `192`**（§7.9 最適化を前提とした最小構成向け）。同時再生 50 ギルド超を想定する大規模運用では 384〜512 へ引き上げる。

##### `lavalink/application.yml` の最適化

```yaml
lavalink:
  server:
    frameBufferDurationMs: 1000        # 既定 5000。Player ごとのフレームバッファを縮小（−1〜2 MB / Player）
    bufferDurationMs: 400              # 既定 400。これ以上は下げない（再生スタッタの原因）
    playerUpdateInterval: 5            # 既定 5（秒）。Bot への状態通知間隔
    trackStuckThresholdMs: 10000       # 既定 10000。これ未満で stuck 判定するとフリッカー
    useSeekGhosting: false             # 不要（メモリより CPU 寄りだが念のため無効）
```

- **§3.1.2 の無効化対象**（Twitch / Bandcamp / Vimeo / HTTP 直リンク / Local files）は `enabled: false`。
- 有効化対象（§3.1.1）以外を読み込まないことで JVM の起動メモリを削減。
- LavaSrc の検索結果上限は `searchLimit: 10` に抑制。

### 7.9 メモリ最適化の実装指針

§7.8.4 の概算は以下の最適化を実装した状態を前提とする。**Phase 1 で全項目を実装する**。

#### 7.9.1 discord.py Client の最小化

- `discord.Intents` は `guilds` と `voice_states` のみ有効化。`members` / `presences` / `message_content` / `messages` は無効。
- `chunk_guilds_at_startup=False`（起動時に全メンバーを取得しない）。
- `member_cache_flags=discord.MemberCacheFlags.none()`（メンバーキャッシュを完全に無効化）。
- `max_messages=None`（メッセージキャッシュを完全に無効化。既定 1000 件 / ギルドの内部キャッシュを廃止）。
- ギルド情報のフェッチは必要最小限。`Guild.fetch_*` 系を使う場合もキャッシュしない。

#### 7.9.2 キュー / 履歴のメモリ効率

- `MAX_QUEUE_SIZE`（既定 `500`）を超える追加は拒否し、エフェメラルで「キューが上限に達しています」を返す。**主目的は異常追加・荒らし対策**であり、メモリ削減効果は二次的（500 件 × ~500 byte = 約 250 KB のオーダー）。
- 履歴は直近 1 曲のみ（§7.4）。
- **`__slots__` の徹底**: 以下のクラスをすべて `dataclass(slots=True)` で定義し、`__dict__` を持たない（`__slots__` 直書きでも可）:
  - `QueueTrack`（キュー内の 1 曲。タイトル / URL / 長さ / 追加者 ID / artwork URL / Lavalink encoded blob を含む）
  - `GuildPlaybackState`（Player ラッパー: ループモード / シャッフル状態 / 履歴 / 現在曲）
  - `RoutingState`（ギルド × Client の対応関係キャッシュ。§7.7.4）
  - `PanelState`（Control Panel メッセージ ID / 編集タイムスタンプ）
- Track の Lavalink encoded blob（300 byte 程度）は **保持する** — 再生開始時の再検索はネットワーク往復 + 結果ブレを招くため、メモリより信頼性を優先。
- キュー削減後は `list.clear()` ではなく **新しいリストを代入**して `list` の内部容量も縮める（`self._queue = []`）。

#### 7.9.3 asyncpg のチューニング

- `DB_POOL_SIZE` 既定 `4`（音量変更は秒間 1 件未満想定）。
- `statement_cache_size=0`（既定 100 → 0）。本 Bot のクエリは **SELECT volume** と **UPSERT volume** の 2 種類しかなく、cache の意義がない。各接続のメモリオーバーヘッド（statement metadata）を削れる。
- `max_inactive_connection_lifetime=300`（5 分でアイドル接続を切断）。
- `min_size=1, max_size=DB_POOL_SIZE` でアイドル時は 1 接続まで縮める。

#### 7.9.4 Wavelink / Player のクリーンアップ

- 自動切断時（§7.1）は `Player.disconnect()` に加えて、Wavelink Pool から **明示的に Player を破棄**して GC を促す。
- キュー・履歴は `_queue = []` で再代入（§7.9.2）。
- **`gc.collect()` の呼び出しは限定する**:
  - 自動切断（§7.1）/ 全 Player 破棄時のみ `gc.collect(generation=2)` を 1 回。
  - 個別の `/skip` `/remove` ごとに呼ぶのは禁止（10〜100ms のレイテンシが入るため）。
- **循環参照の回避**: Control Panel View ↔ Player ↔ Bot Client の間に強参照ループを作らない。Panel 側から Player への参照は **`weakref.ref(player)`** で保持し、Player が破棄されたら Panel は自動で「終了状態」に遷移する。
- Control Panel メッセージは破棄せず、ボタンを disabled にして残す（編集 1 回で済む）。

##### Embed 再生成のコスト管理

- progress 更新（5 秒ごと）で **Embed オブジェクトを毎回新規生成しない**。
  - `discord.Embed` のテンプレを `Player` インスタンスに 1 つ保持し、`embed.set_field_at(...)` で位置・時間欄のみ更新。
  - サムネイル URL や曲タイトルなど不変フィールドは曲変更時のみ更新。
- これにより progress 更新ごとのアロケが数十〜数百個の小オブジェクトから数個に減る → GC 圧低下。

#### 7.9.5 Python ランタイム調整

Bot の Dockerfile / Railway 環境で以下を設定:

| 変数 | 値 | メモリ効果 |
|---|---|---|
| `MALLOC_TRIM_THRESHOLD_` | `131072` | glibc が未使用ヒープを OS へ早めに返却（−5〜10%）|
| `PYTHONMALLOC` | `malloc` | pymalloc を無効化し、glibc の trim と整合 |
| `PYTHONOPTIMIZE` | `2` | 起動時に `assert` と docstring を削除（−3〜5 MB）|
| `PYTHONDONTWRITEBYTECODE` | `1` | `.pyc` を作らない（イメージサイズ削減）|

##### uvloop の導入

- 標準 `asyncio` イベントループの代わりに **uvloop** を使う。Python 起動時に以下を呼ぶ:
  ```python
  import uvloop
  uvloop.install()
  ```
- メモリ効果は −5〜10 MB（イベントループ実装が C 側で完結し、Python オブジェクトが減る）。
- 副次効果として I/O 性能も向上（特に Wavelink WebSocket 越しのトラフィック）。
- Linux Docker のみでサポート。macOS / WSL2 でも動くが、Windows ネイティブはスキップ（フォールバックとして標準 asyncio）。

##### 定期的な GC

- アイドル時に `gc.collect(generation=2)` を **5 分ごとに 1 回**実行する軽量タスクを追加（§7.9.4 の即時 GC とは別）。
- 全 Client がアイドル状態（Player 0 個）の時のみ実行し、再生中は走らせない。

#### 7.9.6 Docker イメージ

- ベース: `python:3.12-slim`。
  - `python:3.12-alpine` は不採用。`asyncpg` などのネイティブ依存を musl libc 上でビルドすると失敗 / 互換性問題が出やすいため。slim は glibc ベースで本 Bot の依存と整合。
- マルチステージビルドで `build-essential` 等を最終イメージから除外。
- 依存インストール: **`uv sync --frozen --no-cache --no-dev`** を使用。`--no-dev` で `pytest` / `mypy` / `ruff` 等の開発依存を本番イメージから除外（−10〜30 MB）。
- 最終ステージで以下を削除:
  ```dockerfile
  RUN find /usr/local/lib/python3.12 -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true \
      && rm -rf /usr/share/doc /usr/share/man /usr/share/locale /usr/share/info \
      && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*
  ```
- ロケール / man / doc / locale の削除で 20〜30 MB 削減。

#### 7.9.7 監視・観測の軽量化（省 CPU + リーク検知）

- progress 更新（5 秒間隔・§5.2.1）は **再生中の Player を持つギルドのみ**走らせる。アイドル中の Client では完全停止 → 不要なタイマーオブジェクトとアロケが発生しない。
- ソフトリミット監視（§7.8.2）の RSS 計測タスクは **§7.8.2 のみが正**。本サブセクションでは重複規定しない。

##### メモリリーク検知（任意・調査用）

長期稼働で RSS が右肩上がりに増えるケース（Discord キャッシュ漏れ・循環参照・コルーチン未完了等）を診断するため、`APP_ENV=development` または環境変数 `TRACEMALLOC=1` のときのみ起動時に `tracemalloc.start(25)` を呼ぶ。

- `/admin memstat`（管理者専用 slash command, Phase 2）で `tracemalloc.take_snapshot()` の上位 20 アロケート位置を返す。
- 本番では既定 OFF（tracemalloc 自体が 5〜10% のオーバーヘッドを持つため）。
- 短期調査では `kill -USR1 <pid>` で snapshot をログに出力する手動フックも用意（`signal.signal(SIGUSR1, ...)`）。

---

## 8. 設定 (環境変数)

### 8.1 ボット (Bot サービス)

| 変数名 | 必須 | 既定値 | 説明 |
|---|---|---|---|
| `DISCORD_TOKENS` | ✅ | — | カンマ区切りの Discord Bot トークン列。1〜`MAX_BOT_INSTANCES` 個（§7.7） |
| `LAVALINK_HOST` | ✅ | — | Lavalink ホスト（例: `lavalink.railway.internal`） |
| `LAVALINK_PORT` | — | `2333` | Lavalink ポート |
| `LAVALINK_PASSWORD` | ✅ | — | Lavalink 認証パスワード |
| `LAVALINK_SECURE` | — | `false` | `true`/`false` |
| `DATABASE_URL` | ✅ | — | `postgres://user:pass@host:port/db` 形式 |
| `DB_POOL_SIZE` | — | `4` | asyncpg コネクションプールサイズ（§7.9.3） |
| `MAX_BOT_INSTANCES` | — | `4` | 同時起動可能な Discord Client 数の上限（§7.7） |
| `MAX_PLAYERS_PER_BOT` | — | `50` | 1 Client あたり同時再生ギルド数の上限（§7.8） |
| `MAX_QUEUE_SIZE` | — | `500` | キューに追加できる最大曲数（§7.9.2） |
| `MEMORY_LIMIT_MB` | — | なし | プロセスのハードメモリ上限。設定時 `RLIMIT_AS` で強制（§7.8.1） |
| `MEMORY_SOFT_LIMIT_PERCENT` | — | `90` | ソフトリミット閾値（%）。新規 Player 作成を停止（§7.8.2） |
| `DEV_GUILD_ID` | — | なし | 開発用ギルド ID。設定時はそのギルドに即時 slash command 登録 |
| `APP_ENV` | — | `production` | `production` / `development` |
| `LOG_LEVEL` | — | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

#### 8.1.1 Python ランタイム調整（Dockerfile / Railway 環境で固定設定）

メモリ節約のため以下を **コンテナ起動時に常時設定**する（§7.9.5）。ユーザーが変更する想定はない。

| 変数 | 値 | 効果 |
|---|---|---|
| `MALLOC_TRIM_THRESHOLD_` | `131072` | glibc が未使用ヒープを OS へ早めに返却 |
| `PYTHONMALLOC` | `malloc` | pymalloc を無効化 |
| `PYTHONDONTWRITEBYTECODE` | `1` | `.pyc` を作らない |
| `PYTHONUNBUFFERED` | `1` | 標準出力をバッファリングしない |

### 8.2 Lavalink サービス

| 変数名 | 必須 | 既定値 | 説明 |
|---|---|---|---|
| `LAVALINK_SERVER_PASSWORD` | ✅ | — | 上の `LAVALINK_PASSWORD` と一致させる（Railway の Shared Variable で同一値を参照することを推奨） |
| `LAVALINK_MAX_HEAP_MB` | — | `192` | JVM ヒープ上限（`-Xmx<n>m`）。大規模運用は 384〜512 へ。Railway 上の RAM 設定より 10% ほど小さくすること |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | — | — | LavaSrc 経由で Spotify を有効化する場合（§3.1.1）。未設定時は Spotify URL のみ無効、他音源は引き続き利用可 |
| `APPLE_MUSIC_TOKEN` | — | — | LavaSrc 経由で Apple Music を有効化する場合（§3.1.1）。未設定時は Apple Music URL のみ無効、他音源は引き続き利用可 |

> Deezer は認証情報不要のため環境変数なし。SoundCloud / YouTube / YouTube Music も同様に認証不要（§3.1.1）。

`.env.example` を同梱する。

---

## 9. プロジェクト構成

```
music-bot/
├── SPEC.md                   ← 本書
├── README.md                 ← セットアップ手順
├── pyproject.toml            ← 依存・ツール設定 (uv / ruff / mypy / pytest)
├── uv.lock
├── Dockerfile                ← Bot 本体
├── docker-compose.yml        ← ローカル一括起動
├── .env.example
├── .gitignore
├── .dockerignore
├── railway.toml              ← Railway デプロイ設定（bot サービス用、§10.7.1）
├── .github/
│   ├── workflows/
│   │   ├── ci.yml            ← ruff + mypy + pytest + docker build
│   │   └── docker.yml        ← main へのマージ時に GHCR へ push（任意）
│   ├── dependabot.yml        ← pip / docker / actions 週次更新
│   └── pull_request_template.md
├── lavalink/
│   ├── Dockerfile            ← Lavalink イメージ（プラグイン込み）
│   ├── application.yml       ← Lavalink 設定
│   └── railway.toml          ← Railway デプロイ設定（lavalink サービス用、§10.7.1）
├── src/
│   └── music_bot/
│       ├── __init__.py
│       ├── __main__.py       ← `python -m music_bot` で起動
│       ├── supervisor.py     ← N Client を asyncio.gather で起動・監視・再起動 (§7.7)
│       ├── bot.py            ← 単一 Client 用のファクトリ・Cog ローダ
│       ├── config.py         ← 環境変数の読み込み・検証 (pydantic-settings)
│       ├── db.py             ← asyncpg Pool, マイグレーション, get_volume / set_volume
│       ├── lavalink.py       ← Wavelink Pool 接続 (全 Client で共有), 共通プレイヤー操作
│       ├── memory_guard.py   ← RLIMIT_AS 設定 + psutil ソフトリミット監視 (§7.8)
│       ├── logging_setup.py  ← logging 設定。レコードに bot_name を埋める
│       ├── cogs/
│       │   ├── __init__.py
│       │   ├── playback.py   ← play / playnext / playnow / search / pause / resume / skip / skipto / back / replay / seek / forward / rewind / stop
│       │   ├── queue.py      ← queue / nowplaying / clear / remove / move / jump / shuffle / loop / grab
│       │   ├── voice.py      ← join / disconnect / stayalone / 自動切断ハンドラ
│       │   └── volume.py     ← /volume (DB 永続化)
│       ├── ui/
│       │   ├── __init__.py
│       │   ├── panel.py      ← Music Control Panel (PersistentView) — Embed 生成・ボタン定義・更新ループ (§5.2)
│       │   ├── queue_view.py ← Queue ビュー (ephemeral) — select menu + 操作ボタン (§5.4)
│       │   └── modals.py     ← Add / Search / Volume モーダル (§5.3)
│       ├── routing.py        ← マルチボット振り分けロジック (§7.7.4) と per-guild Lock
│       └── utils/
│           ├── __init__.py
│           ├── embeds.py     ← 共通 Embed 生成
│           ├── format.py     ← 時間整形・プログレスバー
│           └── checks.py     ← ボイス権限チェック (app_commands.check)
└── tests/
    ├── conftest.py
    ├── test_format.py
    ├── test_db.py            ← testcontainers-postgres を使った統合テスト（CI でも Docker が必要）
    ├── test_routing.py       ← 振り分けアルゴリズム (§7.7.4) のユニットテスト
    └── test_volume_cog.py    ← pytest-mock で discord.py / asyncpg をモック
```

---

## 10. Railway デプロイ

本 Bot は **Railway 専用設計**。ローカル開発（§11）以外のデプロイ先として Railway 以外を想定しない。

### 10.1 サービス構成

3 サービスを **同一 Railway プロジェクト内・同一 Region** に配置する。

| サービス | 種別 | 公開ポート | 説明 |
|---|---|---|---|
| `bot` | **Worker**（HTTP 公開なし） | なし | 本リポジトリの `Dockerfile` をビルド。Discord Gateway への outbound 接続のみ |
| `lavalink` | Worker（内部 HTTP のみ） | 内部 `2333` | `lavalink/Dockerfile` をビルド。Public Networking は **OFF** |
| `postgres` | **Managed Database** | — | `Add Service` → `Database` → `Add PostgreSQL`。RAM は Railway が管理（直接指定不可） |

#### 10.1.1 サービス作成手順

1. Railway プロジェクトを新規作成し Region を指定（§10.2）。
2. `Add Service` → `GitHub Repo` → 本リポジトリを選択、サービス名 `bot`:
   - Settings → Build → **Builder: Dockerfile**、**Dockerfile Path: `Dockerfile`**
   - Settings → Networking → Public Networking **OFF**
   - Settings → Deploy → Healthcheck **無効**（HTTP 公開なし）
3. もう一度 `Add Service` → 同じ GitHub Repo、サービス名 `lavalink`:
   - Settings → Build → **Builder: Dockerfile**、**Dockerfile Path: `lavalink/Dockerfile`**
   - Settings → Networking → Public Networking **OFF**、Internal Networking ON
   - Settings → Deploy → **Healthcheck 無効**（理由は §10.5.1）
4. `Add Service` → `Database` → `Add PostgreSQL` で `postgres` サービスを追加。

### 10.2 Region 配置

- 全 3 サービスを **同一 Region** に配置することが必須（クロスリージョンは RTT +50〜100ms でボイス品質劣化）。
- 推奨 Region:
  - 主に日本 / アジア向けユーザー: `asia-southeast1` (Singapore) — Discord の東京クラスタへ最寄り
  - 主に北米向け: `us-west2` (Oregon)
  - 主に欧州向け: `europe-west4` (Amsterdam)
- Lavalink ↔ Discord 間のレイテンシがボイス品質を支配するため、**ユーザー基地と Discord クラスタの両方に近い Region** を選ぶ。

### 10.3 環境変数の連携

#### 10.3.1 Reference Variables（サービス間参照）

bot サービスの環境変数で他サービスを **明示的に参照**する（Railway は自動注入しない）:

```
# bot service
DATABASE_URL=${{Postgres.DATABASE_URL}}
LAVALINK_HOST=lavalink.railway.internal
LAVALINK_PORT=2333
```

#### 10.3.2 Shared Variables（複数サービス共有）

`LAVALINK_PASSWORD`（bot 側）と `LAVALINK_SERVER_PASSWORD`（lavalink 側）は **同一値**である必要があるため、Project Settings → Shared Variables で 1 箇所に定義し両サービスから参照:

```
# bot service
LAVALINK_PASSWORD=${{shared.LAVALINK_PASSWORD}}

# lavalink service
LAVALINK_SERVER_PASSWORD=${{shared.LAVALINK_PASSWORD}}
```

`SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` / `APPLE_MUSIC_TOKEN` も同じパターンで二重管理を防ぐ。

#### 10.3.3 Environments（本番 / 開発）

Railway の Environments で `production` と `development` を分離。

| 環境変数 | production | development |
|---|---|---|
| `DISCORD_TOKENS` | 本番 Bot のトークン群 | 開発用 Bot のトークン（別 Application） |
| `DEV_GUILD_ID` | 未設定（グローバル登録） | 開発ギルドの ID（即時反映） |
| `APP_ENV` | `production` | `development` |
| `LOG_LEVEL` | `INFO` | `DEBUG` |

### 10.4 リソース目安

§7.8.4 の概算 RSS に **+25% の起動・GC スパイク余裕**を加え、Railway Pro の **実プラン（vCPU + RAM 単位 GB）** に丸めて選ぶ。Railway の選択可能 RAM プラン: **0.5 / 1 / 2 / 4 / 8 / 16 / 32 GB**。`MEMORY_LIMIT_MB` は割当の 90%（OOM Killer の前に `MemoryError` で graceful に落ちる、§7.8.1）。

| サービス | 構成 | 算出 RSS | +25% | Railway plan | `MEMORY_LIMIT_MB` |
|---|---|---|---|---|---|
| bot (1〜2 Client, 通常〜最大) | N=1〜2, G ≤ 50 | 〜243 MB | 〜304 MB | **0.5 GB** | `460` |
| bot (4 Client, 通常) | N=4, G=10×4 | 202 MB | 253 MB | **0.5 GB** | `460` |
| bot (4 Client, 最大) | N=4, G=50×4 | 522 MB | 653 MB | **1 GB** | `920` |
| lavalink (最小構成) | 同時 〜50 Player | — | — | **0.5 GB** | `LAVALINK_MAX_HEAP_MB=384` |
| lavalink (大規模) | 同時 100+ Player | — | — | **1 GB** | `LAVALINK_MAX_HEAP_MB=768` |
| postgres | データ < 1 MB | — | — | **Managed**（RAM 直接指定なし、使用量ベース課金） | — |

- bot は基本 0.5 GB で十分。4 Client × 50 ギルド最大想定のみ 1 GB へ。
- lavalink の `LAVALINK_MAX_HEAP_MB` は割当 RAM の **〜75% 程度**（JVM オーバーヘッド分を残す）。
- §7.9 ランタイム調整（uvloop / `MALLOC_TRIM_THRESHOLD_` 等）は Dockerfile / 起動コードに焼き込み済み、Railway 側で個別設定は不要。
- 本番ログで RSS / 割当 比が常時 0.5 を下回るならプランダウン可。

### 10.5 ヘルスチェック

| サービス | Railway 設定 |
|---|---|
| `bot` | **Healthcheck 無効**（HTTP 公開なし）。クラッシュ検知 = プロセス exit のみ。1 Client でも生存していれば exit しない（§7.7.3） |
| `lavalink` | **Healthcheck 無効**（理由は §10.5.1） |
| `postgres` | Managed のため設定不要 |

#### 10.5.1 Lavalink で Railway ヘルスチェックを使わない理由

Lavalink の REST API は `/v4/*` 配下がすべて `Authorization: <password>` ヘッダー必須。残る `/version` は認証不要だが、Public Networking OFF の private-networking サービスでは Railway の port 検出が安定せず、Lavalink が 2333 で listen していてもヘルスチェックが届かないケースがある。

代替として **bot 側の HTTP プリフライト**（§7.5）が Lavalink への到達性を起動時に検証している:

- bot は `setup_hook` で Lavalink の `/version` に GET を投げ、5 回まで 30 秒間隔でリトライ
- 全失敗で bot プロセスが exit → Railway の `restartPolicyType = "ON_FAILURE"` で再起動

Lavalink が落ちているのに bot だけ生きている状態は、bot 側のプリフライトと Wavelink ノード切断検知（§7.5 後半）の二段で検出されるため、Railway 側の重複チェックは不要。

### 10.6 エグレス課金の注意（重要）

ボイス再生は継続的に Discord へデータを送信するため、Railway の **エグレス課金（$0.10/GB）** に注意。

#### 10.6.1 試算

1 Player あたり Discord へのボイスデータ送信: **約 64 kbps**（Opus stereo 標準）。月間 24/7 再生想定:

| 同時再生 Player 数 | 月間エグレス | 概算費用 |
|---|---|---|
| 1 Player 24/7 | 約 20 GB | $2/月 |
| 10 Player 24/7 | 約 200 GB | $20/月 |
| 50 Player 24/7 | 約 1 TB | $100/月 |
| **200 Player 24/7**（N=4 × G=50 最大） | **約 4 TB** | **$400/月** |

実際の利用は 24/7 ではなく数時間/日が一般的なため、上記の **1/4〜1/8** が現実値の目安（最大想定でも $50〜$100/月）。

#### 10.6.2 対策

- マルチボット最大 (N=4 × G=50) は **エグレスが運用コストの主因**になりうる。実運用前に Railway Usage ダッシュボードで実測を確認。
- 同時再生数を抑える運用（DJ 1 人ルール、`/stayalone` を多用しない、`MAX_PLAYERS_PER_BOT` の絞り込み）が有効。
- 大規模運用が必要なら Hetzner / VPS / bare metal 等の egress 無料/低価格な選択肢も検討する。

### 10.7 ビルド構成

#### 10.7.1 `railway.toml` を 2 つ用意

| ファイル | 対象 | Dockerfile Path | Healthcheck |
|---|---|---|---|
| `railway.toml`（リポジトリ root） | bot サービス | `Dockerfile` | 無効 |
| `lavalink/railway.toml` | lavalink サービス | `lavalink/Dockerfile` | 無効（§10.5.1） |

`railway.toml`（bot 用）:
```toml
[build]
builder = "dockerfile"
dockerfilePath = "Dockerfile"

[deploy]
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 10
```

`lavalink/railway.toml`（lavalink サービスの **Root Directory を `/lavalink`** に設定して読み込ませる）:
```toml
[build]
builder = "dockerfile"
# Root Directory が /lavalink なのでパスは Dockerfile（コンテキスト相対）
dockerfilePath = "Dockerfile"

[deploy]
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 10
# Healthcheck は意図的に無効。理由は §10.5.1。
```

> **重要**: lavalink サービスの **Settings → Source → Root Directory** を `/lavalink` にすること。Railway はリポジトリ root の `/railway.toml` を全サービスに適用しようとするので、Root Directory を切り替えないと `lavalink/railway.toml` が読み込まれず、bot 用の Dockerfile が誤ってビルドされる。

`railway.json` でも同等の構文で記述可。本仕様では `.toml` を採用。

#### 10.7.2 ビルドキャッシュ

- Railway は Docker BuildKit のレイヤキャッシュを利用。Dockerfile では **`uv.lock` を先にコピーしてから `uv sync`** することで依存層をキャッシュし、コード変更時の再ビルドを高速化。
- 初回ビルドは Lavalink プラグイン（LavaSrc / youtube-source）のダウンロードで 5〜10 分かかる場合がある。Railway のビルドタイムアウト（既定 30 分）には十分余裕。

#### 10.7.3 デプロイ方法

- **GitHub Integration**（推奨）: 各 Environment が指定ブランチを watch。`production` は `main` を、`development` は別ブランチ（例: `develop`）を watch するのが一般的。
- `railway up` CLI は手動デプロイ専用。CI からは使わない（§12）。

### 10.8 PostgreSQL 接続詳細

- **接続 URL**: Reference Variable `${{Postgres.DATABASE_URL}}` を bot に注入。Railway 内部 URL の形式: `postgresql://user:pass@postgres.railway.internal:5432/railway?sslmode=disable`。
- **SSL**: 内部ネットワーク経由は `sslmode=disable`（既定）。外部接続なら `sslmode=require`。本 Bot は内部接続前提。
- **`max_connections`**: Railway Postgres 既定は **50**。本 Bot の使用量は最大 N=4 Client × `DB_POOL_SIZE=4` = **16 接続**。十分余裕あり。`MAX_BOT_INSTANCES` を 8 以上に増やす場合は接続数を再計算（合計が 40 を超えないこと推奨、管理用に 10 余裕を残す）。

### 10.9 ログ・モニタリング

- **ログ保持期間はプラン依存**:
  - Hobby: 直近 100k ログ行（数日〜1 週間相当）
  - Pro: 直近 30 日
- 重要なエラーは Railway ログタブで遡及確認。メモリリーク調査（§7.9.7）等で 1 週間以上の遡及が必要な場合は Pro プラン推奨。
- Railway の Usage タブで **RSS / CPU / Egress** の時系列を監視。Egress が想定外に伸びていたら §10.6 の対策を見直す。

### 10.10 24/7 稼働 / Sleep

- Railway は通常コンテナを Sleep させない（Heroku / Render と異なる）。本 Bot の常時稼働前提と整合。
- ただし **Hobby Trial** は無料クレジット消費で停止する。Pro プランまたはクレジット追加が必要。
- Railway 自体の障害時は隣接 Region への自動フェイルオーバーは無い。SLA は Railway の SLA に従う。

---

## 11. ローカル開発手順 (`README.md` で詳述)

1. `cp .env.example .env` し、`DISCORD_TOKENS`（カンマ区切りで 1 個以上）と `DEV_GUILD_ID` を記入。
2. `docker-compose up --build` で bot / lavalink / postgres が起動。
3. slash command は Bot 起動時に `tree.sync()` で自動登録。`DEV_GUILD_ID` 指定時はそのギルドへ即時反映、未指定時はグローバルへ登録（最大 1 時間で反映）。
4. **招待 URL の発行**: Discord Developer Portal の各 Bot Application の OAuth2 → URL Generator で `bot` + `applications.commands` スコープと必要権限（`Send Messages` / `Embed Links` / `Connect` / `Speak` / `Use Voice Activity`）を選択して生成。`README.md` には N 個分の URL を列挙する。
5. Discord 上でコマンド実行。

依存だけローカルで管理したい場合:

```bash
uv sync                       # pyproject.toml + uv.lock から仮想環境を作成
uv run python -m music_bot    # Bot を直接起動（Lavalink / DB は別途用意）
uv run ruff check .           # Lint
uv run mypy src               # 型チェック
uv run pytest                 # テスト
```

---

## 12. CI / CD (GitHub Actions)

### 12.1 ワークフロー一覧

| ファイル | トリガー | 目的 |
|---|---|---|
| `.github/workflows/ci.yml` | `push` (任意ブランチ) / `pull_request` | lint + 型チェック + テスト + Docker build 検証 |
| `.github/workflows/docker.yml` | `push` to `main` / タグ `v*` | GHCR (`ghcr.io/<org>/music-bot:<sha>` および `:latest` / `:vX.Y.Z`) へ multi-arch イメージを push |

Railway へのデプロイは GitHub Integration で自動化（main へのマージで自動デプロイ）するため、CD は GitHub Actions 側に実装しない。

### 12.2 `ci.yml` の構成

```yaml
name: ci
on:
  push:
  pull_request:

jobs:
  lint-and-type:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --frozen
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy src

  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_PASSWORD: test
          POSTGRES_DB: music_bot_test
        ports: [5432:5432]
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    env:
      DATABASE_URL: postgres://postgres:test@localhost:5432/music_bot_test
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --frozen
      - run: uv run pytest --cov=music_bot --cov-report=term-missing

  docker-build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - run: docker build --target=runtime -t music-bot:ci .
      - run: docker build -t lavalink:ci ./lavalink
```

- 3 ジョブを並列実行。`docker-build` は GHCR push を伴わない検証のみ。
- `test` ジョブは PostgreSQL を service コンテナで立てる。Lavalink を要する統合テストは Phase 1 ではユニットテスト + DB 統合テストのみとし、Lavalink は除外（モックする）。
- `testcontainers-postgres` は `tests/test_db.py` で使用しているが、CI では service コンテナの方が起動が速いため testcontainers は dev 用途のみとする。

### 12.3 `docker.yml` の構成（任意機能）

- `linux/amd64` のみビルド（Railway は amd64 のため）。
- GHCR への push はリリース時の固定タグ用途。Railway は GitHub 連携で main 直結するため通常運用では未使用。

### 12.4 補助設定

| ファイル | 内容 |
|---|---|
| `.github/dependabot.yml` | `pip` / `docker` / `github-actions` の週次更新 PR |
| `.github/pull_request_template.md` | チェックリスト（テスト追加・SPEC.md 更新・Phase 区分） |
| `pyproject.toml` の `[tool.ruff]` / `[tool.mypy]` / `[tool.pytest.ini_options]` | 各ツール設定をすべて単一ファイルに集約 |

---

## 13. 非機能要件

| 項目 | 要件 |
|---|---|
| 応答時間 | コマンド受信から ack まで 3 秒以内（discord.py の interaction 仕様に準拠） |
| 同時再生ギルド数 | 1 Client あたり 50 ギルド・256 MB で安定動作（§7.8.4）。N Client ならその N 倍 |
| メモリ上限 | `MEMORY_LIMIT_MB` で強制可能（§7.8.1）。超過時は `MemoryError` → Railway 再起動 |
| マルチボット規模 | 同一プロセスで最大 `MAX_BOT_INSTANCES`（既定 4）の Client を並走 |
| アップタイム | Railway の `restart: always` に依存。1 Client 障害でプロセス全体は停止しない（§7.7.3） |
| ログ保管 | Railway のログタブに保管。保持期間はプラン依存（Hobby: 直近 100k 行 / Pro: 30 日）。§10.9 |
| セキュリティ | トークンは環境変数のみ。リポジトリにコミットしない |

---

## 14. 実装フェーズ

### Phase 1 (MVP, 本仕様書のスコープ)

- §5 の操作 UI（Music Control Panel + モーダル + Queue ビュー + slash フォールバック）
- §6 のビジュアル仕様（色・更新ポリシー・アクセシビリティ）
- §4 の DB スキーマ（`(guild_id, bot_id)` 複合主キー）
- §7 動作仕様の細部（自動切断・権限チェック・Loop・履歴・エラー・ロギング）
- §7.7 マルチボット（1 プロセス N Client、`DISCORD_TOKENS` 列指定、振り分けロジック）
- §7.8 メモリ上限・リソース制御（ハード / ソフトリミット）
- **§7.9 メモリ最適化の全項目**（最小 intents・キャッシュ無効・slots・glibc trim・SerialGC 等）
- §10 の Railway デプロイ
- §11 のローカル開発フロー
- §12 の CI（lint / typecheck / test / Docker build）

### Phase 2 (将来)

- `/filter`, `/equalizer`
- `/lyrics`（外部 API: Genius / lrclib）
- `/247` (24/7 モード)
- 履歴を 10 件まで拡張
- Web ダッシュボード（音量・ループ設定の閲覧）

---

## 15. 仕様外（実装しないもの）

- prefix コマンド (`m!play` 等)
- DJ ロール / 投票スキップ
- セッション保存系（`/savequeue` `/loadqueue` `/247` 等）
- 多言語対応 UI（Phase 1 は英語＋日本語の混在エンベッド）

---

## 16. 参考

- Lavalink v4: https://lavalink.dev
- Wavelink: https://github.com/PythonistaGuild/Wavelink
- discord.py: https://discordpy.readthedocs.io/
- LavaSrc プラグイン: https://github.com/topi314/LavaSrc
