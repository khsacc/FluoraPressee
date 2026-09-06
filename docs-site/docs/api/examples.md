---
sidebar_position: 13
title: curlでの実行例
description: 主要エンドポイントをcurlで呼び出す例
---

# curlでの実行例

```bash
# 状態確認
curl -H "X-API-Key: <キー>" http://<IP>:8765/status

# キャッシュ済みカメラ情報
curl -H "X-API-Key: <キー>" http://<IP>:8765/hardware/camera

# 分光器のライブ詳細情報
curl -H "X-API-Key: <キー>" "http://<IP>:8765/hardware/spectrometer?refresh=true"

# 実行中・保存済みconfig
curl -H "X-API-Key: <キー>" http://<IP>:8765/config

# Load Configurationと同じactive候補
curl -H "X-API-Key: <キー>" http://<IP>:8765/configurations

# configurationを適用してpixel軸で取得
curl -X POST -H "X-API-Key: <キー>" -H "Content-Type: application/json" \
  -d '{"configuration_id":"cfg_...", "axis_mode":"pixel"}' \
  http://<IP>:8765/acquire

# データ取得+フィット+圧力算出
curl -X POST -H "X-API-Key: <キー>" -H "Content-Type: application/json" \
  -d '{
        "exposure_time_s": 0.5, "accumulations": 3,
        "dark": {"mode": "reuse_loaded"},
        "fit_function": "Pseudo Voigt", "fit_peak_count": 2,
        "baseline_model": "constant",
        "fit_range": {"start": 690, "end": 700},
        "sensor": "ruby", "pressure_scale": "ruby_shen_2020",
        "zero_pressure_peak": 694.30
      }' \
  http://<IP>:8765/acquire/pressure
```
