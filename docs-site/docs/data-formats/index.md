---
sidebar_position: 1
title: 保存データの形式
description: FluoraPresséeが保存する各種ファイルの形式一覧
---

# 保存データの形式

FluoRaPressée は、測定・解析の過程でいくつかの種類のファイルをディスクへ保存します。
ここでは、それぞれのファイルの拡張子・内部構造・記録される項目についてまとめます。

Configurationファイルと`spectrometerConfig.json`を除き、保存場所は基本的にユーザーがファイル保存ダイアログで指定したフォルダです。
Configurationファイルのみ、OSのユーザー別application data領域に自動保存されます。
`spectrometerConfig.json`はリポジトリルート直下に保存されます。

## ファイル一覧

| ファイル | 生成タイミング | 形式 | 説明 |
|---|---|---|---|
| [スペクトルデータファイル](spectrum-data.md) | 「Save Data」、連続測定の各フレーム | テキスト（`#`コメントヘッダー + CSVデータ） | 1Dスペクトルまたは2Dイメージ本体 |
| [バックグラウンドファイル](background.md) | バックグラウンド取得直後の保存ダイアログ | JSON | 差し引き用のバックグラウンドスペクトル |
| [フィッティング結果ファイル](fitting-results.md) | 「Save Data」時（フィット有効時）、連続測定 | テキスト（単発）／CSV（連続測定サマリ） | ピークフィッティングと圧力計算の結果 |
| [連続測定ログファイル](sequential-log.md) | 連続測定の終了時 | テキスト | 実行条件と、保存された各フレームのファイル名・タイムスタンプ一覧 |
| [Configurationファイル](configuration.md) | 較正ダイアログの「Save and apply」 | JSON + SQLiteカタログ | grating・中心波長・ROI・横軸較正のバージョン管理付き記録 |
| [spectrometerConfig.json](spectrometer-config.md) | 初回起動時のセットアップウィザード完了時、Hardware Configuration適用時 | JSON | メーカー・接続方法・grating構成・ROI初期値・冷却設定などハードウェアそのものの構成 |

これらのデータをHTTP経由で取得・適用する場合は[API](../api/index.md)を参照してください。
