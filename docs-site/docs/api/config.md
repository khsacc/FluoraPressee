---
sidebar_position: 6
title: "GET /config"
description: 起動時設定と保存済み設定を返すエンドポイント
---

# GET /config

現在のプロセスで有効な設定と、`spectrometerConfig.json` に保存されている設定を返します。

```json
{
  "schema_version": 1,
  "captured_at": "2026-07-22T15:30:00+09:00",
  "source_file": "spectrometerConfig.json",
  "active_config": {
    "model": "PrincetonInstruments",
    "com_port": "COM3",
    "grating": [],
    "flip_x": false,
    "default_temperature": -65
  },
  "stored_config": {
    "model": "PrincetonInstruments",
    "com_port": "COM3",
    "grating": [],
    "flip_x": false,
    "default_temperature": -65
  },
  "restart_required": false,
  "pending_restart_keys": [],
  "redacted_fields": []
}
```

- `active_config`: 実行中プロセスへ現在適用されている設定。
  グレーティング、ROI、表示設定などのライブ変更は反映しますが、起動時にしか読まれない接続設定は起動時の値を返します。
- `stored_config`: 現在の `spectrometerConfig.json` の内容。
- `restart_required`: `model`, `com_port`, `dll_path`, `PIcam_dll_path`, `camera_serial_number` のいずれかに未反映の変更があるか。
- `pending_restart_keys`: 再起動待ちのキー一覧。
- `redacted_fields`: `api_key`, `password`, `token`, `secret` を名前に含むキーをレスポンスから除外した場合のパス一覧。
  APIキーは `spectrometerConfig.json` ではなく別ファイル（[クライアントと認証](clients.md)）に
  保存されているため、そもそもこのレスポンスには現れません。
