---
sidebar_position: 7
title: spectrometerConfig.jsonの仕様
description: メーカー別の接続設定項目、ファイル内の各キーの意味、grating配列やhardware_identityの構造、メーカー別の設定例
---

# spectrometerConfig.jsonの仕様

このページは、`spectrometerConfig.json` のファイル形式そのものを詳しく知りたい人（直接編集したい人、外部ツールから読み書きしたい人など）向けのリファレンスです。
GUIのセットアップウィザードや設定変更ダイアログからの操作方法は[ハードウェア設定](../hardware-config.md)を参照してください。

## メーカー別の接続設定項目

| メーカー | 入力項目 |
|---|---|
| Andor | `ShamrockCIF.dll` が置かれているディレクトリ |
| Princeton Instruments | 分光器（Acton SPシリーズ）のCOMポート、PICam Runtimeのディレクトリ、カメラのシリアル番号（接続カメラが1台のみなら省略可） |
| Ocean Optics | デバイスのシリアル番号（省略可＝最初に見つかったデバイスを使用）、seabreezeバックエンド（通常は空欄のままでよい） |

## ファイルの仕様

| キー | 型 | 対象 | 説明 |
|---|---|---|---|
| `model` | string | 共通 | `"Andor"` / `"PrincetonInstruments"` / `"OceanOptics"`。`src/hardware/camera.py`・`src/hardware/spectrometer.py` のファクトリ関数がこの値でドライバ実装を選び分けます。 |
| `dll_path` | string | Andor | `ShamrockCIF.dll` の場所。ディレクトリ・ファイルへのフルパスのどちらでも構いません（ディレクトリを指定した場合は内部で自動的に `ShamrockCIF.dll` が結合されます）。 |
| `com_port` | string | Princeton Instruments | 分光器のシリアルポート。例: `"COM3"` |
| `PIcam_dll_path` | string | Princeton Instruments | PICam Runtimeのディレクトリ。 |
| `camera_serial_number` | string | Princeton Instruments | カメラのシリアル番号。空文字（または省略）の場合、接続カメラが1台のみであれば自動選択されます。 |
| `serial_number` | string \| null | Ocean Optics | デバイスのシリアル番号。`null`（または省略）の場合、最初に見つかったデバイスを使用します。 |
| `seabreeze_backend` | string \| null | Ocean Optics | `"cseabreeze"` / `"pyseabreeze"`。通常は `null` のままでよい。 |
| `grating` | array | 共通 | grating設定の配列。要素の形式は下記「`grating` 配列」を参照。 |
| `flip_x` | boolean | 共通 | スペクトルを左右反転して表示するか。 |
| `default_temperature` | int | 冷却制御に対応する機種 | 起動時の冷却器目標温度（℃）。冷却制御を持たない機種（Ocean Optics、および温度制御非対応と検出されたPrinceton Instrumentsカメラ）では省略されます。 |
| `default_fan_mode` | string | Andor | `"full"` / `"low"` / `"off"`。Andor SDK2固有の冷却ファン制御で、Princeton Instruments/Ocean Opticsの設定には含まれません。 |
| `hardware_identity` | object | 共通 | 直近に確認された分光器・カメラの `model` / `serial_number` を記録する情報。下記「`hardware_identity`」を参照。 |
| `correct_dark_counts` | boolean | Ocean Optics | 取得時にdark count補正を要求するか（既定 `true`）。ウィザード・GUIには入力欄がなく、ファイルに手動で追記した場合のみ有効です。デバイスが非対応であればコンソールに警告を出し、補正なしで取得します。 |
| `correct_nonlinearity` | boolean | Ocean Optics | 同上、非線形性補正版。 |

### `grating` 配列

各要素は次の3項目を持ちます。

- `index`: 分光器に送信される物理的なタレット/スロット番号です。
  配列内の並び順と一致している必要はありません。
- `grooves`: 溝本数（grooves/mm）。
  Ocean Opticsは可動gratingを持たないため常に `0` の内部的なプレースホルダーで、編集不要です。
- `defaultROI`: `{"from": <開始行>, "to": <終了行>}`。
  このgratingが選択されたときに適用されるデフォルトの垂直ROI（ピクセル行範囲）です。

### `hardware_identity`

`{"spectrometer": {"model": ..., "serial_number": ...}, "camera": {"model": ..., "serial_number": ...}}` という形式で、直近に確認された機器の識別情報を保持します。

- ウィザードの「Read parameters from connected hardware」で読み取れた場合はその値が、読み取れなかった場合は `null` が入ります。
- ウィザード完了後、アプリ本体が初めて実機に接続した際にまだ何も記録されていなければ、検出結果がそのまま静かに記録されます。
- 記録済みの内容と実際に接続された機器の model/serial number が食い違う場合は警告ダイアログが表示され、意図した変更であればその場で記録を更新するか選べます。
  設定ファイルが自動的に書き換わることはありません。

## 設定例

### Andor

```json
{
    "model": "Andor",
    "dll_path": "C:\\Program Files\\Andor SDK\\Shamrock64\\ShamrockCIF.dll",
    "grating": [
        {"index": 1, "grooves": 2400, "defaultROI": {"from": 80, "to": 100}},
        {"index": 2, "grooves": 1800, "defaultROI": {"from": 115, "to": 130}},
        {"index": 3, "grooves": 1200, "defaultROI": {"from": 113, "to": 125}}
    ],
    "flip_x": true,
    "default_temperature": -65,
    "default_fan_mode": "full",
    "hardware_identity": {
        "spectrometer": {"model": "Shamrock 500i", "serial_number": "SR5001234"},
        "camera": {"model": "DU401", "serial_number": "CCD-12345"}
    }
}
```

### Princeton Instruments

```json
{
    "model": "PrincetonInstruments",
    "com_port": "COM3",
    "PIcam_dll_path": "C:\\Program Files\\Princeton Instruments\\PICam\\Runtime",
    "camera_serial_number": "0412060001",
    "grating": [
        {"index": 1, "grooves": 1200, "defaultROI": {"from": 100, "to": 140}},
        {"index": 2, "grooves": 1800, "defaultROI": {"from": 100, "to": 140}}
    ],
    "flip_x": false,
    "default_temperature": -65,
    "hardware_identity": {
        "spectrometer": {"model": "SP-2750", "serial_number": null},
        "camera": {"model": "ProEM 1600", "serial_number": "0412060001"}
    }
}
```

### Ocean Optics

```json
{
    "model": "OceanOptics",
    "serial_number": null,
    "seabreeze_backend": null,
    "grating": [
        {"index": 1, "grooves": 0, "defaultROI": {"from": 0, "to": 1}}
    ],
    "flip_x": false,
    "hardware_identity": {
        "spectrometer": {"model": "USB2000", "serial_number": "USB2+F02651"},
        "camera": {"model": "USB2000", "serial_number": "USB2+F02651"}
    }
}
```

Ocean Opticsは分光器とカメラが一体になった固定分光器のため、`hardware_identity` の `spectrometer`/`camera` には同じ機器の情報が入ります。
また、FluoraPressée自身のネオン較正（「Calibrate x-axis」）を適用するまでは、X軸には装置内蔵の工場較正済み波長軸がそのまま表示されます（これは「未較正」とは異なり、Ocean Optics自身の較正データに基づく正しい波長軸です）。

## 設定を変更する

初回セットアップ後に構成を変更する方法は2通りあります。

1. **メニューバー → Hardware Configuration**: Hardware/Connection・Grating・Display/Defaultsの3タブから編集し、Apply/OKで保存します。
   ただし `model` / `com_port` / `dll_path` / `PIcam_dll_path` / `camera_serial_number` / `serial_number` / `seabreeze_backend` はカメラ／分光器スレッドの構築時に一度だけ読み込まれる値のため、変更を反映するにはアプリの再起動が必要です（ダイアログもその旨のメッセージを表示します）。
   gratingや `flip_x`、`default_temperature` の変更は再起動なしで即座に反映されます。
2. **`spectrometerConfig.json` を直接編集**して、アプリを再起動する。

## 読み込みに関する補足

- `spectrometerConfig.json` は存在するがJSONとして読み込めない（壊れている）場合、アプリはエラーで停止せず、内部的なAndor向けデフォルト値（ファイルへは書き込まれません）にフォールバックして起動します。
  使われている設定とファイルの中身が食い違って見える場合は、まずファイルの構文が正しいか確認してください。
- `grating` が数値の配列だけの旧形式で保存されている場合は、読み込み時に自動的に現在の `{"index", "grooves", "defaultROI"}` 形式へ変換され、ファイルに書き戻されます。
