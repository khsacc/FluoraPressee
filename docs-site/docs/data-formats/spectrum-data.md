---
sidebar_position: 2
title: スペクトルデータファイル
description: 1Dスペクトル・2Dイメージ本体を保存するテキストファイルの形式
---

# スペクトルデータファイル

メイン画面の「Save Data」ボタン、およびAnalysis Modeでの保存はすべてこの形式で書き出されます。連続測定中は
各フレームがこの同じ形式で自動保存されます（ファイル名の規則は[連続測定ログファイル](sequential-log.md)を
参照）。

拡張子は `.txt` ですが、中身は「`#`で始まるコメント行のヘッダーブロック」+「カンマ区切りのデータ行」という
CSV風のプレーンテキストです。既定のファイル名は `data_YYYYMMDD_HHMMSS_mmm.txt`（保存ダイアログを開いた
瞬間のタイムスタンプ）です。

## ヘッダー

データ行の前に、`#`で始まる次の項目が順番に並びます（`Excitation Wavelength` / `hardware_metadata` は
条件によって省略されます）。

```
# Timestamp: 2026-07-22 15:30:00
# Grating: 1200 grooves/mm
# Spectrometer Mode: Wavelength
# Centre Wavelength: 694.0 nm
# Acquisition Time: 0.1 s
# Accumulations: 1
# Calibration Coefficients (c0, c1, c2: y = c0 + c1x + c2x^2): 673.3405851432854, 0.020990883361968825, -2.889725985123467e-07
# ROI Start (Vertical Pixel): 113
# ROI End (Vertical Pixel): 125
# Measurement Mode: 1D (ROI)
# hardware_metadata: {"schema_version":1,"captured_at":"2026-07-22T15:30:00.123+09:00", ...}
# Wavelength_or_Pixel,Intensity
673.63,1122
673.65,1130
...
```

- `Spectrometer Mode` が `Raman shift` の場合、`Centre Wavelength` の代わりに `Excitation Wavelength`
  （nm）と `Centre Raman shift`（cm<sup>-1</sup>）の2行が挿入されます。
- `Calibration Coefficients` は横軸較正が未適用の場合 `# Calibration Coefficients: None` になります。
- `ROI Start` / `ROI End` は、その時点でROIスピンボックス（Vertical Start/End）に入力されている値を
  そのまま記録します。2D表示やFull ROI表示で保存した場合でも、この2行はスピンボックスの値のままです。
  実際にどのモードで取得したデータかは次の `Measurement Mode` 行（`2D` / `1D (Full)` / `1D (ROI)`）を
  参照してください。
- `hardware_metadata` は、取得時のカメラ・分光器の詳細情報をJSONとして1行に埋め込んだものです（存在しない
  場合は行ごと省略されます）。内容は下記「hardware_metadata の内容」を参照してください。

## データ列（1Dスペクトル）

列見出しの行（ヘッダーブロックの最後の`#`行）は2種類あります。

- 横軸が波長またはpixelの場合: `Wavelength_or_Pixel,Intensity`
- 横軸がRaman shiftで、かつ較正済みの場合: `Raman_shift_cm-1,Intensity`

`Spectrometer Mode: Raman shift` であっても較正が未適用の場合は、横軸はpixel値になるため列見出しは
`Wavelength_or_Pixel,Intensity` のままです。横軸の列は保存時点で完全に解決済み（nm / cm<sup>-1</sup> / pixel）の値
であり、読み込み側でグレーティングや較正係数を別途参照する必要はありません。「Flip X-axis」が有効な場合も、
横軸・縦軸ともに反転済みの状態でそのまま保存されます。

バックグラウンドの差し引きが有効かつロード済みバックグラウンドの長さがデータと一致する場合は、
差し引き前後の値を含む4列形式になります。

```
# Wavelength_or_Pixel,Intensity_Subtracted,Intensity_Raw,Background
673.341,2,1031,1029
673.362,3,1035,1032
...
```

それ以外の場合（バックグラウンド無効、または長さ不一致）は、常に2列形式（`Intensity`列は表示中の値 —
バックグラウンドが正常に差し引かれていればその差分、そうでなければ生カウント）で保存されます。

## データ列（2Dイメージ）

ヘッダーブロックは1Dと共通ですが、最後の行が列見出しではなく `2D Image Data` という区切り行になり、
続けて検出器の生画像データ（行×列の輝度値グリッド、カンマ区切り）がそのまま出力されます。

Analysis Modeのファイル選択は、この最後のヘッダー行が `2D Image Data` かどうかで2Dファイルを判別し、
1Dスペクトル用の一覧からは除外します。同様に、`Grating:` 行が存在しないファイル（バックグラウンドJSONや
Configuration JSON、フィッティング結果ファイルなど）も1Dスペクトル一覧には表示されません。

## hardware_metadata の内容

`hardware_metadata` は、取得時のカメラ・分光器の詳細情報をまとめたJSONで、次のような構造を持ちます（実際に含まれるフィールドは接続機種 — Andor / Princeton Instruments /
Ocean Optics — によって異なり、非対応の項目はキーごと省略されるか `null` になります）。

```json
{
  "schema_version": 1,
  "captured_at": "2026-07-22T15:30:00.123+09:00",
  "camera": {
    "identity": {"controller_model": "...", "model": "...", "serial_number": "..."},
    "detector_size_px": {"width": 1340, "height": 400},
    "pixel_pitch_um": {"width": 20.0, "height": 20.0},
    "exposure_s": 0.1,
    "temperature": {"current_c": -65.0, "setpoint_c": -65.0, "status": "stabilised"},
    "roi": {"mode": "1d_roi", "horizontal_start": 0, "horizontal_end": 1340, "vertical_start": 113, "vertical_end": 125},
    "binning": {"horizontal": 1, "vertical": 12},
    "accumulations": 1,
    "accumulation_mode": "software_sum"
  },
  "spectrometer": {
    "serial_number": "...",
    "grating": {"index": 1, "grooves_per_mm": 1200, "blaze": "500nm"},
    "center_wavelength_nm": 694.0,
    "wavelength_limits_nm": [200.0, 1000.0]
  },
  "axis": {
    "source": "neon_polynomial",
    "configuration_id": "cfg_...",
    "configuration_slot_id": "slot_...",
    "configuration_label": "1200 g/mm | 694.000 nm | ROI 113–125",
    "calibration_coefficients": {"c0": 673.34, "c1": 0.0210, "c2": -2.89e-07},
    "calibration_unit": "Wavelength"
  },
  "background": {
    "loaded": true,
    "used": true,
    "match": true,
    "mismatched_fields": [],
    "comparison_level": "hardware_metadata",
    "source_file": "background_20260722_150000_acq_0.100s_accum_1_ROI_from_113_to_125.txt"
  }
}
```

- `axis.source` は横軸較正の由来を表し、`neon_polynomial`（ネオン較正）、
  `emission_standard_polynomial`（較正ダイアログの標準線較正）、`loaded_configuration`
  （Load Configurationで読み込んだ較正）、`api_inline_calibration`（APIの非推奨インラインcalibration）、
  もしくは較正未適用時の `pixel` / `hardware_shamrock` / `oceanoptics_native`（Ocean Opticsの
  工場較正済み波長軸）のいずれかを取ります。
- `background` セクションは、この測定でバックグラウンドがロード・使用されていたか、および現在の取得条件と
  ロード済みバックグラウンドの条件が一致していたか（`mismatched_fields`に不一致項目名を列挙）を記録します。
  バックグラウンドファイル自体（`background`セクションを持たない）ではこのセクションは省略されます。
