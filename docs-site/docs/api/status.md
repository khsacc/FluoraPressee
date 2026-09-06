---
sidebar_position: 4
title: "GET /status"
description: 現在の状態を返すエンドポイント
---

# GET /status

現在の状態を返します。

**レスポンス例:**
```json
{
  "busy": false,
  "camera_connected": true,
  "exposure_time_s": 0.1,
  "calibration": {"applied": true, "unit": "Wavelength", "label": "manual-20260709"},
  "roi": {"mode": "1d_roi", "start": 100, "end": 140},
  "background": {"loaded": false, "metadata": null},
  "configuration": {
    "configuration_id": "cfg_...", "slot_id": "slot_...",
    "axis_mode": "calibrated", "calibration_applied": true,
    "unit": "Wavelength"
  },
  "hardware_state": {
    "grating_index": 2, "grooves_per_mm": 1200,
    "actual_center_wavelength_nm": 694.0,
    "roi_mode": "1d_roi", "roi_start": 100, "roi_end": 140
  },
  "instrument_state_token": "3f2a91c0:14"
}
```
- `instrument_state_token`は**不透明な文字列**で、等値比較のみに使います。
  [`POST /acquire`](acquire.md)の`expected_state_token`にそのまま渡すと、
  ここで確認した状態のままであることを保証して取得できます。
