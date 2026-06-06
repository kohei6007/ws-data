# Investment Journal Docs

別端末（RDPなし）で claude.ai chat から参照可能な投資ジャーナルの公開コピー。

## ファイル一覧

| ファイル | 内容 | raw URL |
|---|---|---|
| `JOURNAL_EXPORT.md` | コンソリデート版（claude.ai 用） | [link](https://raw.githubusercontent.com/kohei6007/ws-data/main/docs/JOURNAL_EXPORT.md) |
| `SESSION_HANDOFF.md` | 完全版引き継ぎ書 | [link](https://raw.githubusercontent.com/kohei6007/ws-data/main/docs/SESSION_HANDOFF.md) |
| `lessons.md` | 取引から得た教訓 | [link](https://raw.githubusercontent.com/kohei6007/ws-data/main/docs/lessons.md) |
| `weekly_latest.md` | 最新週次まとめ | [link](https://raw.githubusercontent.com/kohei6007/ws-data/main/docs/weekly_latest.md) |

## claude.ai での使い方

### 方法 A：URL を直接 claude.ai に投げる
```
このURLを読んで要約して：
https://raw.githubusercontent.com/kohei6007/ws-data/main/docs/JOURNAL_EXPORT.md
```
→ claude.ai が WebFetch で取得して内容把握

### 方法 B：Project Knowledge にアップロード
1. claude.ai の「投資シグナル分析」プロジェクトを開く
2. Files → 旧 JOURNAL_EXPORT.md を×で削除
3. PCから新版を再アップロード

### 方法 C：内容をコピペ
- OneDrive 上のファイルから内容コピー
- claude.ai チャットに直接貼り付け

## 更新フロー

Claude Code（PC）で「JOURNAL_EXPORT を更新して push」と頼む：
1. OneDrive 上のオリジナル更新
2. このフォルダにコピー
3. `git up` で push
4. 数秒後、raw URL から最新版アクセス可
