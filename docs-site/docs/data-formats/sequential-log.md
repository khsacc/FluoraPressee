---
sidebar_position: 5
title: 連続測定ログファイル
description: 連続測定の実行条件と保存フレーム一覧を記録するテキストファイルの形式
---

# 連続測定ログファイル

連続測定（Sequential measurement）を停止すると、実行全体のサマリが選択ディレクトリ直下に`seq_summary_YYYYMMDD_HHMMSS.txt`（開始時刻ベース）として書き出されます。
ユーザーが「Stop」を押した場合だけでなく、指定枚数（Max frames）に達して自動停止した場合にも書き出されます。

```
Sequential Measurement Summary
Start Time: 2026-07-22 15:30:00
End Time: 2026-07-22 15:35:12
Exposure Time: 0.1 s
Accumulations: 1
Skip Frames: 9
------------------------------
Filename,Saved Time
seq_00000_20260722_153005_012.txt,2026-07-22 15:30:05.012
seq_00001_20260722_153015_034.txt,2026-07-22 15:30:15.034
...
```

- 先頭のブロックは実行条件（開始・終了時刻、露光時間、積算数、Skip frames設定）です。
- `------------------------------` の区切り線の後に、実際に保存されたフレームのファイル名と保存時刻の一覧がCSVとして続きます（フィッティングに失敗し保存自体がスキップされたフレームは含まれません）。

## 各フレームのファイル名

連続測定中に保存される各フレームは、[スペクトルデータファイル](spectrum-data.md)と同じ形式で、ファイル名は次の規則に従います。

```
seq_{5桁ゼロ埋めフレーム番号}_{YYYYMMDD_HHMMSS_mmm}.txt
```

「Save fitting results」がオンの場合、各フレームごとに個別の[`<フレームファイル名>_fitting_results.txt`](fitting-results.md#単発測定-_fitting_resultstxt) も同時に生成されます。
これは実行全体をまとめた `fitting_seq_summary_*.txt`（[フィッティング結果ファイル](fitting-results.md#連続測定サマリ-fitting_seq_summary_開始日時txt)を参照）とは別に、フレームごとに追加で作成されるものです。
