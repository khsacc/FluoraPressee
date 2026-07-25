---
sidebar_position: 1
title: メニューバー
description: メイン画面上部のメニューバー（Hardware / API / Tools）の全体像
---

# メニューバー

メイン画面（`SpectrometerGUI`）の上部には、常設のツールバー・パネルには収まらない設定・診断・補助機能をまとめたメニューバーがあります。
**Analysis Mode**（`analysis_main.py` またはTools → Analysis Modeで開くウィンドウ）にはこのメニューバーはありません。

メニューは次の3つです。

| メニュー | 項目 | 内容 |
|---|---|---|
| [Hardware](hardware/index.md) | Hardware Configuration... / Instrument Status... / Manage Configuration Files... | 接続機器の設定編集、状態診断、保存済みConfigurationの管理 |
| [API](api.md) | Regenerate Key | HTTP API認証キーの再発行 |
| [Tools](tools.md) | Analysis Mode… | 装置接続なしでスペクトルを解析するAnalysis Modeを開く |

:::note
Hardwareメニューの **Hardware Configuration...** のみ、連続測定中またはAPIサーバー起動中はグレーアウトして操作できません（`spectrometerConfig.json`の書き換えを、実行中の測定やAPI経由の操作と競合させないためです）。
他の項目はこの間も操作できます。
:::
