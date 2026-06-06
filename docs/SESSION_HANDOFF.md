# 投資プロジェクト セッション引き継ぎ書

**最終更新: 2026-06-05**

別セッション or 別タイミングで作業を引き継ぐ時、まずこのファイルを読んで全体像を把握する。

---

## 1. ユーザープロフィール

- **投資スタイル**：テクニカル分析メインの個別株スイングトレード（保有期間 2日〜3週間）
- **取引商品**：**キンカブ**（金額指定単元株未満取引）
- **売買タイミング**：**寄付き成行のみ**（場中売買不可、仕事中）
- **判断タイミング**：前夜〜翌朝寄付き前までに完了必須
- **マニュアル**：`C:\Users\ko-ohnishi\Desktop\ai\prj_self\投資マニュアル.md`（GFS/ブンゼミ系手法、買い/売りサイン・損切り・地合い・暴落判定）

## 2. 銘柄選定戦略（コントラリアン × モメンタム）

- メディア（CNBC・Reuters・Bloomberg）で**頻出する主役銘柄は追わない**（既に過熱・高PER）
- **主役銘柄と同じテーマだが、メディアで言及されていない銘柄**を狙う（未話題ゾーン）
- watchlist は意図的に「テーマ沿い + 未話題」で構成
- 半導体材料系の中型・中小型が多いのはこの戦略のため

## 3. 現在の watchlist（12銘柄）

`C:\Users\ko-ohnishi\Desktop\ai\prj_self\ws-data\watchlist.yaml`

```
半導体装置：     8035 東京エレクトロン、6920 レーザーテック
計測検査：       6754 アンリツ
半導体材料：     4368 扶桑化学、4971 メック、4099 四国化成HD、4182 三菱ガス化学、7995 バルカー
アルミ素材：     5741 UACJ
FA・産業：       6503 三菱電機、6506 安川電機
衛星・防衛：     9412 スカパーJSAT
```

GitHub Public Repo: `https://github.com/kohei6007/ws-data`（毎日朝/夕の cron で snapshot.json 自動生成）

## 4. 現在の保有状況（2026-06-05 時点）

**保有銘柄: なし**（4182 三菱ガス化学を 6/5 寄付きで -6.93% 損切り済）

### 直近のクローズ済取引

| 銘柄 | エントリー | 売却 | 結果 |
|---|---|---|---|
| 4186 東京応化工業 | 5/25 11,350円 | 5/29 11,146円 | **-1.80%**（教科書通り撤退で当日損切ライン到達回避） |
| 4182 三菱ガス化学 | 6/2 5,371円 | 6/5 4,999円（寄付・底値） | **-6.93%**（売却判断は正解、寄付タイミング不運） |

詳細：`OneDrive/claude-output/trade-journal/closed/`

## 5. 構築済システム

### 5-1. analyze.py（自動分析スクリプト）v3

3箇所同期：
- PC: `~/.claude/skills/investment-signals/scripts/analyze.py`（subprocess 版）
- claude.ai 用: `claude-output/investment-signals-claude-ai-project/analyze.py`（self-contained）
- GitHub Actions 用: `Desktop/ai/prj_self/ws-data/analyze.py`（同上、push 対象）

機能：
- yfinance で日足/週足/月足取得 + kabutan で当日終値補完（遅延対策）
- MA方向・トレンド周期・5MA下抜け検出
- 出来高ゾーン
- 決算日チェック
- **ファンダメンタル評価**（PER/PBR/ROE/EPS成長/売上成長）
- 直近5日スコープ（v2）
- hard/soft フラグ分離
- verdict: 🟢🟡⚪⛔⚠️🚨

### 5-2. GitHub Actions（自動 snapshot 生成）

リポジトリ: `kohei6007/ws-data`（Public）
- cron: JST 平日 15:30 (場後) + 翌朝 8:30 (寄り前)
- snapshot.json を自動生成・コミット
- claude.ai の Project からは `raw.githubusercontent.com` 経由で fetch（認証不要）

### 5-3. claude.ai Project「投資シグナル分析」

ファイル構成：
- `analyze.py`（参照用）
- `fetch_snapshot.py`（snapshot.json 取得 + 整形）
- `references/*.md`（マニュアル準拠ルール 6ファイル）
- 手順（PROJECT_INSTRUCTIONS.md の内容）

スマホからの利用：「監視リスト全部」「○○分析して」「地合い教えて」

### 5-4. youtube_transcript.py

- 場所: `~/.claude/skills/investment-signals/scripts/youtube_transcript.py`
- 依存: `youtube-transcript-api`（pip 済）
- 日経CNBC YouTube URL → 字幕取得 → `claude-output/cnbc-transcripts/` 保存
- 用途：日次CNBC視聴の自動要約・継続論調分析

### 5-5. データ蓄積構造

```
OneDrive/claude-output/
├── market-journal/             ← 市況データ蓄積
│   ├── 2026/06/                ← 日次ファイル
│   │   ├── YYYY-MM-DD_bloomberg.md
│   │   ├── YYYY-MM-DD_us.md
│   │   ├── YYYY-MM-DD_jp.md
│   │   ├── YYYY-MM-DD_cnbc.md
│   │   └── YYYY-MM-DD_kabutan.md
│   ├── weekly/                 ← 週次サマリ
│   └── monthly/                ← 月次サマリ
├── cnbc-transcripts/           ← CNBC 字幕原本
├── trade-journal/              ← 取引履歴
│   ├── active/                 ← 保有中
│   ├── closed/                 ← クローズ済
│   ├── learnings/lessons.md    ← 教訓集
│   └── README.md
├── investment-signals-claude-ai-project/  ← claude.ai 用ファイル群
└── investment-signals-github-actions/     ← Actions 用ミラー
```

## 6. 日々の運用フロー

### 朝（火-土）
ユーザーが投げる：
1. **「ロイター X 米国」+ 記事本文** → `_us.md` 保存
2. **「ブルームバーグ X 今朝の5本」+ 記事本文** → `_bloomberg.md` 保存

私の処理時、**必ず以下をチェック**（キンカブ寄付き売買向け）：
- 米国半導体個別の前夜動向（ブロードコム・NVDA・AMD・MU・QCOM）
- シカゴ先物 vs SOX の乖離（二極化警告）
- watchlist 保有銘柄の **5MA との距離**（+1%以内なら警戒ライン）
- 同セクター仲間の前日動向
- 保有銘柄について「**翌朝寄付き売り予約が必要か**」を明示
- → シグナル発動 = 寄付き売り予約は **必須行動**（次善策ではない）

### 夕方以降（月-金）
ユーザーが投げる：
1. **「ロイター X 日本」+ 記事本文** → `_jp.md` 保存
2. **「CNBC X」+ YouTube URL** → 字幕取得 → `_cnbc.md` 要約保存
3. **「株探決算 X」** → 株探 fetch → `_kabutan.md` 保存

### 週次（土曜）
1. **「Bloomberg 週次 X/Y」+ 本文** → `_bloomberg_weekly.md` 保存（毎週土曜のBloomberg配信）
2. ユーザーが「週次まとめお願い」 → `weekly/YYYY-Wxx.md` 生成

### 月次（月初）
ユーザーが「先月の振り返り」 → `monthly/YYYY-MM.md` 生成

### 取引時
- エントリー：「XXXX を IN: 日付・価格・理由」→ `trade-journal/active/` にファイル作成
- 経過：「XXXX 経過」 → 日次追記
- 売却：「XXXX OUT: 日付・価格・理由」→ `closed/` 移動 + 学び記録

## 7. 損切ライン併記ルール

| ライン | 計算 | 役割 |
|---|---|---|
| -5%（マニュアル基準） | エントリー × 0.95 | 教科書ライン、常に把握 |
| -6% | エントリー × 0.94 | キンカブ寄付き売却のバッファ |
| -7%（実運用上限） | エントリー × 0.93 | 絶対防衛線 |

エントリー時・経過時・判断時の3箇所で必ず3段階表示。

## 8. 主要な売却シグナル（マニュアル §9 + 独自）

### マニュアル §9 売りサインの3段階
- **1次**：トレンド周期 8〜10本 = 利確検討
- **2次**：陰線2本連続 + ヨコヨコ = 警戒
- **3次**：5MA陰線下抜け = 売却

### 独自・補助シグナル（教訓蓄積）

| シグナル | 内容 |
|---|---|
| **VSP（Volume Surge Pivot）** | 過去 3-4ヶ月の出来高急増日の高値・中央値を上値抵抗・下値サポートとして活用。詳細：`references/volume-surge-pivot.md` |
| **直近高値（1-2ヶ月）+ 過去最大出来高日（3-4ヶ月）の両方を意識** | 上値抵抗を二重で確認 |
| **同セクター先行サイン** | 同セクター銘柄が前日下落 → 翌日自分の銘柄も売り圧力示唆 |
| **5MA との距離 +1%以内 + 同セクター仲間下落** | 翌朝寄付き売り予約の警戒ライン |

### 撤退条件（4-5シグナル揃いで確定撤退）
1. §9 3次（5MA陰線下抜け）
2. 直近高値（1-2ヶ月）未突破
3. 過去最大出来高日（3-4ヶ月）高値未突破
4. 当日陰線
5. 米半導体ショック等の外部要因

→ 4以上揃ったら**翌朝寄付き売却必須**

## 9. 蓄積した教訓集

`trade-journal/learnings/lessons.md`

1. 5MA陰線下抜けの翌日は売り
2. 「上がるか下がるかは誰にもわからない」を尊重
3. 未話題ゾーンは連れ高に乗りやすいが主役が崩れると先に崩れる
4. パーフェクトオーダーは万能ではない
5. データ反映の時差に注意（yfinance遅延）
6. Volume Surge Pivot（VSP）を判定軸に加える
7. 直近高値の上値抵抗を意識
7.5. **過去3-4ヶ月前の出来高最大日も上値抵抗として認識する**（重要）
8. フライングエントリーの心理を排する
8.5. **キンカブ・寄付き売買のみ前提の取引設計**（最重要）

## 10. 未着手・保留タスク

### 後日着手候補
- **analyze.py に「シグナル予兆検知」「同セクター仲間取り込み」を追加**（B案、優先度中）
- **株探プレミアム「明日の好悪材料」記事の取り込み**（プレミアム会員限定で取れない）
- **claude.ai の Analysis tool で kabutan 経由データ取得が可能か再検証**

### ローテーション情報を活かす検討
- 6/4-6/5 で「AI半導体 → バリュー・出遅れ・グロース」へ資金シフトの兆し
- 続けば watchlist の AI半導体偏重を見直す余地
- 注目テーマ：**サーバー冷却（水冷式）**、**ソフトウェア再評価**

## 11. 注意事項

- **判定で「買え／売れ」と断定しない**：「マニュアルの○○条件に該当」と中立表現
- **最終判断はユーザー**：私はあくまで判定提示と分析
- **金融取引は私から執行しない**：注文・売買は必ずユーザー自身が行う
- **データ反映遅延に注意**：yfinance / kabutan / Reuters の最新性を毎回確認
- **寄付き売却の底値リスク**を常に意識した運用提案

## 12. キーファイル一覧

| パス | 用途 |
|---|---|
| `C:\Users\ko-ohnishi\Desktop\ai\prj_self\投資マニュアル.md` | マニュアル原本 |
| `C:\Users\ko-ohnishi\Desktop\ai\prj_self\ws-data\` | GitHub リポジトリのローカルクローン |
| `~/.claude/skills/investment-signals/` | PC スキル本体 |
| `OneDrive/claude-output/SESSION_HANDOFF.md` | このファイル |
| `OneDrive/claude-output/market-journal/` | 市況ジャーナル |
| `OneDrive/claude-output/trade-journal/` | 取引ジャーナル |
| `OneDrive/claude-output/cnbc-transcripts/` | CNBC字幕 |
| `OneDrive/claude-output/investment-signals-claude-ai-project/` | claude.ai 用パッケージ |

## 13. 直近の重要イベント・スケジュール

- **2026-06-05 夜（NY時間）**：米雇用統計（5月予想 +8万人）★
- **2026-06-13 頃**：メジャーSQ（来週末）
- **2026-06-17-18 頃**：FOMC（再来週）
- **2026-06-19-20 頃**：日銀金融政策決定会合（再来週）

## 14. このプロジェクトの目的

> マニュアル準拠の機械的判定 + ユーザー独自の戦略観点（コントラリアン×モメンタム）+ 多角的情報源（CNBC・Reuters・Bloomberg・株探）の蓄積 + 取引履歴の学び を組み合わせ、**スイングトレードの勝率を継続的に高める**システムを構築する。
> 最終判断はユーザー、私は情報整理・判定提示・教訓記録の支援役。
