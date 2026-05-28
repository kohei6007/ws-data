"""
GitHub Actions から呼ばれる cron ランナー。

watchlist.yaml を読み、analyze.full_analysis() を実行し、結果を snapshot.json に書き出す。
書き出した snapshot.json を Actions ワークフローが git commit する。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

from analyze import full_analysis


def main():
    root = Path(__file__).resolve().parent
    wl_path = root / "watchlist.yaml"
    snap_path = root / "snapshot.json"

    if not wl_path.exists():
        print("watchlist.yaml not found", file=sys.stderr)
        sys.exit(1)

    with wl_path.open(encoding="utf-8") as f:
        wl = yaml.safe_load(f)

    if not wl.get("enabled", True):
        print("watchlist disabled; skip")
        return

    tickers = [t["code"] for t in wl.get("tickers", [])]
    opts = wl.get("options", {}) or {}
    print(f"Analyzing {len(tickers)} tickers: {tickers}")

    result = full_analysis(
        tickers,
        with_chart=opts.get("with_chart", False),
        market_only=opts.get("market_only", False),
    )

    # 銘柄名・備考をJSONにマージ（snapshot閲覧時に分かりやすく）
    name_map = {t["code"]: t for t in wl.get("tickers", [])}
    for tr in result.get("tickers", []):
        meta = name_map.get(tr.get("ticker"), {})
        if meta:
            tr["name"] = meta.get("name", "")
            tr["note"] = meta.get("note", "")

    result["generated_at"] = datetime.utcnow().isoformat() + "Z"
    result["generated_by"] = "github-actions"

    with snap_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"wrote {snap_path} ({snap_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
