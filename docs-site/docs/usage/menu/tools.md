---
sidebar_position: 3
title: Toolsメニュー
description: 装置接続なしでスペクトルを解析するAnalysis Modeを開くAnalysis Mode…
---

# Toolsメニュー

## Analysis Mode…

**Tools → Analysis Mode…** は、装置を接続せずに保存済みスペクトルの解析だけを行う**Analysis Mode**（`AnalysisWindow`）を、メイン画面の子ウィンドウとして開きます。
一度開くとウィンドウは使い回され、メニューから再度選んでも新しいウィンドウは作られず、既存のウィンドウが前面に表示されます。

Analysis Modeは、メイン画面と共通のフィッティング設定パネル・圧力計算ウィンドウ・解析ロジック（`DataAnalyzer`）・ファイルI/O（`DataFileIO`）を使いますが、ハードウェア関連のコード（`src/hardware/`, `src/api/`）には一切依存しません。
保存済みスペクトルファイルの横軸（nm / cm⁻¹ / pixel）は保存時点で確定済みのため、読み込み時に較正・グレーティング・ROIの情報を必要としません。

装置を接続せず単独で起動したい場合は、メニューを経由せず `analysis_main.py` を直接実行することもできます。

```powershell
.venv\Scripts\python.exe analysis_main.py
```

フィッティング・圧力計算の使い方自体は[フィッティング](../fitting.md)・[圧力計算](../pressure-calculation/index.md)を参照してください（メイン画面・Analysis Mode共通です）。
