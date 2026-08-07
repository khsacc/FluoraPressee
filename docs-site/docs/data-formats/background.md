---
sidebar_position: 3
title: バックグラウンドファイル
description: 差し引き用バックグラウンドスペクトルを保存するJSONファイルの形式
---

# バックグラウンドファイル

「Acquire Background」でバックグラウンドスペクトルを取得すると開く保存ダイアログで書き出されるファイルです。
拡張子は `.txt` または `.json` を選べますが、中身は常にJSONです。
既定のファイル名は`background_YYYYMMDD_HHMMSS_acq_<露光時間>s_accum_<積算数>_ROI_from_<start>_to_<end>.txt`（Full ROIの場合は `ROI_full`）です。

```json
{
    "detector_settings": {
        "mode": "1D Spectrum (Custom ROI)",
        "roi_start": 113,
        "roi_end": 125
    },
    "acquisition_time": "1.000",
    "accumulations": 1,
    "detector_temperature": "-65.0 °C Stabilised",
    "hardware_metadata": { "...": "..." },
    "signal": [
        1085,
        1089,
        1088,
        1090
    ]
}
```

- `detector_settings.mode` は `"1D Spectrum (Custom ROI)"` または `"1D Spectrum (Full Range Binning)"`のいずれかで、[スペクトルデータファイル](spectrum-data.md)の `Measurement Mode` 行（`1D (ROI)` / `1D (Full)`）とは別の語彙です。
- `detector_temperature` は数値ではなく、保存時点の温度表示ラベルの文字列（例: `"-65.0 °C Stabilised"`、`"-65.0 °C (Cooling Fault)"`、温度取得前なら `"Reading..."` や`"Error"`）をそのまま記録したものです。
  読み込み時にこの値がアプリの動作に使われることはなく、あくまで人が後から確認するための記録です。
- `hardware_metadata` は[スペクトルデータファイル](spectrum-data.md#hardware_metadata-の内容)と同じスキーマですが、`background` セクションを持ちません（バックグラウンド自体には比較対象となるロード済みバックグラウンドが存在しないため）。
  較正が適用されていなければ `axis.calibration_coefficients`は `null` になります。
- `signal` は生カウント値の配列（ROIモードなら選択範囲を1列に合算した値、Full ROIなら検出器全高を合算した値）です。

## 読み込み・整合性チェック

「Load Background」で読み込むと、`loaded_bg_data`（`signal`配列）と`loaded_bg_metadata`（`acquisition_time`, `accumulations`, `mode`, `roi_start`, `roi_end`, `hardware_metadata`, `source_file`）としてアプリに保持されます。
以後、現在の取得条件と比較して露光時間・積算数・ROI・（`hardware_metadata`が両方にあれば）機種識別情報や温度設定値などが一致するかを継続的にチェックし、不一致があれば取得前に警告ダイアログを表示します（API経由では`BackgroundMismatchError` → HTTP 422）。
