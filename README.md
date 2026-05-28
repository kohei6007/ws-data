# investment-signals-data

個人投資（個別株テクニカル分析・スイングトレード）の毎日のスナップショット自動生成。

## 仕組み

1. `watchlist.yaml` に書かれた銘柄を
2. GitHub Actions が cron で実行（JST 15:30 場後 + JST 翌 8:30 寄り前）
3. yfinance で OHLC 取得 → `analyze.py` でマニュアル準拠の判定
4. `snapshot.json` をコミット
5. スマホの claude.ai Project が `raw.githubusercontent.com` から読み取って整形提示

## ファイル

- `watchlist.yaml` — 監視リスト（編集可。スマホ GitHub web からも編集可）
- `analyze.py` — 分析ロジック（PC Claude Code の skill と同期管理）
- `references/*.md` — マニュアル準拠ルール
- `snapshot_runner.py` — Actions から呼ばれるランナー
- `.github/workflows/snapshot.yml` — cron定義
- `snapshot.json` — 自動生成（手で編集しない）

## 監視リスト変更方法

1. ブラウザで GitHub を開く
2. `watchlist.yaml` を編集（Edit ボタン）
3. Commit changes
4. 次回 cron 実行（最長12時間後）または手動実行（Actions タブ → 「Daily stock snapshot」 → 「Run workflow」）で反映

## 手動実行（即時実行したい時）

GitHub web の Actions タブ → 左サイドバー「Daily stock snapshot」 → 右上「Run workflow」ボタン → 数分待つ

## ローカルテスト

```bash
pip install yfinance pandas numpy pyyaml matplotlib
python snapshot_runner.py
cat snapshot.json
```
