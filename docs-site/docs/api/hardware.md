---
sidebar_position: 5
title: 接続機器情報
description: カメラ・分光器の識別情報とライブ状態照会 (GET /hardware/camera, GET /hardware/spectrometer)
---

# 接続機器情報

## GET /hardware/camera

カメラの識別情報、圧力マーカー寸法、露光時間、ROI、binning、温度などを返します。

- `refresh=false`（既定）: カメラスレッドが保持しているキャッシュだけを読み、SDK呼び出しを行いません。
- `refresh=true`: カメラスレッドへライブ状態照会を依頼し、共通形式の `status` を追加します。
  測定・分光器移動・別のライブ照会と競合した場合は `409`、10秒以内に完了しない場合は `504`。

**レスポンス例:**
```json
{
  "schema_version": 1,
  "captured_at": "2026-07-22T15:30:00+09:00",
  "mode": "hardware",
  "operational": true,
  "hardware_connected": true,
  "busy": false,
  "backend": "andor_sdk2",
  "metadata_source": "cache",
  "metadata": {
    "identity": {
      "controller_model": "C-1",
      "model": "DU-401",
      "serial_number": "CAM-001"
    },
    "detector_size_px": {"width": 1024, "height": 127},
    "pixel_pitch_um": {"width": 26.0, "height": 26.0},
    "exposure_time_s": 0.1,
    "accumulations": 3,
    "accumulation_mode": "software_sum",
    "roi": {
      "mode": "1d_roi",
      "horizontal_start": 0,
      "horizontal_end": 1024,
      "vertical_start": 100,
      "vertical_end": 127
    },
    "binning": {"horizontal": 1, "vertical": 27},
    "read_mode": "image",
    "output_rows": 1,
    "software_vertical_sum": false,
    "temperature": {
      "current_c": -64.9,
      "setpoint_c": -65.0,
      "status": "locked"
    }
  },
  "status": null
}
```

`mode` は `hardware` または `debug`。
`operational` はデバッグバックエンドを含めてAPIから利用可能か、`hardware_connected` は物理デバイスに接続されているかを表します。
debugモードでは`operational: true`, `hardware_connected: false` となります。

## GET /hardware/spectrometer

分光器の識別情報、中心波長、現在のグレーティングを返します。
Andor/Princeton Instrumentsとも同じ公開形式で、通常は各コントローラの `get_cached_hardware_metadata()` の結果を利用します。

- `refresh=false`（既定）: DLL/RS-232通信なし。
- `refresh=true`: `get_status_snapshot()` で実機を照会し、`status` を追加します。
  測定・分光器移動・別のライブ照会と競合した場合は `409`、30秒以内に完了しない場合は `504`。

**レスポンス例:**
```json
{
  "schema_version": 1,
  "captured_at": "2026-07-22T15:30:00+09:00",
  "mode": "hardware",
  "operational": true,
  "hardware_connected": true,
  "busy": false,
  "backend": "princeton_acton",
  "metadata_source": "cache",
  "metadata": {
    "identity": {"model": "SP-2750", "serial_number": "SPEC-001"},
    "center_wavelength_nm": 694.0,
    "grating": {"index": 1, "grooves_per_mm": 600, "blaze": null},
    "wavelength_limits_nm": null
  },
  "status": null
}
```

`refresh=true` の `status` は以下の共通形式です。
取得できない機器固有項目は推測せず`state: "unsupported"` とします。
一部項目だけ失敗した場合はその項目を `state: "error"` とし、他の取得結果は返します。

```json
{
  "backend": "princeton_acton",
  "available": true,
  "error": null,
  "sections": {
    "Current position": [
      {
        "key": "centre_wavelength",
        "label": "Centre wavelength",
        "value": 694.0,
        "unit": "nm",
        "state": "ok",
        "error": null
      }
    ]
  }
}
```

未接続はHTTP通信の失敗ではないため `200` を返し、`hardware_connected: false`、`status.available: false` で表現します。
