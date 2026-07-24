---
sidebar_position: 7
title: "POST /acquire"
description: データを1回取得するエンドポイント
---

# POST /acquire

データを1回取得します。

**リクエスト:**
```json
{
  "configuration_id": "cfg_exact_1",
  "axis_mode": "calibrated",
  "exposure_time_s": 0.5,
  "accumulations": 3,
  "dark": {"mode": "none"}
}
```
- `configuration_id`は省略可。省略時は分光器・ROI・横軸を変更せず、現在と同じ条件で取得します。
- `configuration_id`指定時は、[互換性確認、configuration適用、移動完了、取得を一つの排他操作として
  実行します](configurations.md)。同じ物理条件なら移動を省略します。
- `axis_mode`は`configuration_id`指定時だけ使用でき、`"calibrated"`または`"pixel"`。
  省略時は`"calibrated"`。configurationなしで`axis_mode`だけ指定すると`422`。
- `exposure_time_s` / `accumulations` を省略すると、現在GUIに設定されている値がそのまま使われます。
- `dark.mode`:
  - `"none"`（既定）: 減算しません。
  - `"reuse_loaded"`: GUIで現在ロードされている背景ファイルを使って減算します。この取得の
    実際の露光時間・積算数・ROIが、ロード済み背景のメタデータと一致しない場合は
    `422 Unprocessable Entity` を返します（黙って誤った値を減算しません）。どうしても近似的に
    続行したい場合のみ `dark.ignore_mismatch: true` を明示的に指定します（この場合レスポンスに
    `"background_mismatch_warning": true` が付きます）。
  - `"provided"`: `dark.data` に生の暗電流スペクトル配列を直接渡します。長さが取得データと一致
    しなければ `400 Bad Request`。

**レスポンス例:**
```json
{
  "x": [690.01, 690.03, ...],
  "y_raw": [1050.0, 1048.0, ...],
  "y": [1050.0, 1048.0, ...],
  "mode": "1d",
  "exposure_time_s": 0.5,
  "accumulations": 3,
  "detector_temperature_c": -64.8,
  "timestamp": "2026-07-09T10:11:06.677845",
  "configuration": {
    "configuration_id": "cfg_exact_1", "slot_id": "slot_690nm",
    "axis_mode": "calibrated", "calibration_applied": true,
    "unit": "Wavelength"
  },
  "hardware_state": {
    "grating_index": 1, "grooves_per_mm": 600,
    "actual_center_wavelength_nm": 690.0,
    "roi_mode": "1d_roi", "roi_start": 45, "roi_end": 65
  },
  "x_axis": {"source": "calibrated", "unit": "nm", "calibrated": true}
}
```
- `x` は較正済みなら較正後の単位（nm または cm<sup>-1</sup>）、未較正ならpixel番号（Ocean Optics等
  ネイティブ波長軸を持つ機種では、FluoraPressée較正が無くてもその軸を返します）。GUIの
  "Flip X-axis" 設定には依存せず、常に昇順で返します。
- `y_raw` は生データ、`y` はdark減算後（`dark.mode="none"` の場合は `y_raw` と同じ）。
- 2Dイメージモードで取得した場合、`x` は `null`、`y_raw`/`y` はネストした配列（行×列）になります。
- `x_axis`は`x`列の解釈を`configuration.axis_mode`と同じ語彙で明示します:
  - `source`: `"pixel"` / `"native_wavelength"`（FluoraPressée較正なしだが機種側の
    factory-calibratedな波長軸、現状Ocean Opticsのみ） / `"calibrated"`（FluoraPressée較正適用済み）。
  - `unit`: `source="pixel"`なら常に`null`。それ以外はRaman shiftモードなら`"cm-1"`、
    Wavelengthモードなら`"nm"`。
  - `calibrated`: `source == "calibrated"`と等価の真偽値。`false`は「FluoraPressée較正が
    未適用」を意味するだけであり、`native_wavelength`の軸自体が無較正という意味ではありません。

## 関連エンドポイント

- [`POST /acquire/fit`](acquire-fit.md): 取得にピークフィッティングを追加。
- [`POST /acquire/pressure`](acquire-pressure.md): 取得＋フィッティングに圧力算出を追加。
