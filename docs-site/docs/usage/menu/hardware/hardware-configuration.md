---
sidebar_position: 2
title: Hardware Configuration
description: spectrometerConfig.jsonをGUI上で編集するダイアログ（Hardware / Connection・Grating・Display / Defaultsの3タブ）
---

# Hardware Configuration

**Hardware → Hardware Configuration...** は、`spectrometerConfig.json` を再起動なしにGUI上から編集するためのダイアログです。
編集内容は手元の作業用コピーに対して行われ、**Apply** または **OK**を押すまでファイルにもアプリの状態にも反映されません。
**Cancel** を押せば作業用コピーごと破棄されます。

3つのタブで構成されます。

## Hardware / Connection

機種（Andor / Princeton Instruments / Ocean Optics）と、機種ごとの接続設定を編集します。

- **Andor**: `ShamrockCIF.dll` を含むディレクトリのパス
- **Princeton Instruments**: グレーティングコントローラのCOMポート、PICam Runtimeディレクトリ（`picam.dll` / `picam64.dll`）、カメラのシリアル番号（空欄なら1台のみ接続時に自動選択）
- **Ocean Optics**: シリアル番号（空欄なら先頭のデバイスを自動選択）、seabreezeバックエンド（Auto / cseabreeze / pyseabreeze）

各項目の詳しい意味は[spectrometerConfig.jsonの仕様](../../../data-formats/spectrometer-config.md)を参照してください。

## Grating

グレーティングスロットの一覧（物理タレット番号・grooves/mm・既定ROI）を行単位で編集します。
**Add slot** / **Remove selected slot** で行を追加・削除できます。
Index はスロットの並び順ではなく分光器へ送信する物理タレット番号なので、テーブル上の行順と一致している必要はありません。

保存時には以下を検証します。

- 少なくとも1行は必要
- Index は正の整数かつ重複不可
- grooves/mm は正の値（Ocean Opticsの固定分光器のみ例外）
- ROI の from は to より小さいこと

## Display / Defaults

`flip_x`（スペクトルの左右反転表示）と、冷却器の既定目標温度を編集します。
温度制御に対応しない機種（Ocean Optics、または実行時に温度制御なしと判定された機種）ではこの項目自体が非表示になります。

## 保存時の挙動

Andor/Princeton InstrumentsのDLL/COMポート設定が検証に失敗した場合、警告ダイアログで「そのまま保存すると次回起動時にdebugモードへフォールバックする」旨が表示され、続行するか選べます。

保存が成功すると `spectrometerConfig.json` に書き込まれます。
ただし `model` / `com_port` / `dll_path` / `PIcam_dll_path` / `camera_serial_number` / `serial_number` / `seabreeze_backend` はカメラ／分光器スレッドの構築時に一度だけ読み込まれる値のため、変更を反映するには**アプリの再起動が必要**です（保存後にその旨のメッセージが表示されます）。
グレーティング一覧・`flip_x`・既定温度の変更は再起動なしで即座に反映されます。

:::note
連続測定中またはAPIサーバー起動中は、このメニュー項目自体がグレーアウトして開けません。
[API連携](../../../api/index.md)を参照してください。
:::
