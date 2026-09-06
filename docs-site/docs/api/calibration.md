---
sidebar_position: 8
title: "POST /calibration（非推奨）"
description: 較正係数を直接適用する旧エンドポイント
---

# POST /calibration（非推奨）

係数を直接適用する旧エンドポイントです。
新規連携では[configuration関連エンドポイント](configurations.md)を使用してください。
将来削除予定です。

**リクエスト:**
```json
{
  "c0": 694.2, "c1": 0.0153, "c2": 0.0,
  "unit": "Wavelength",
  "laser_wavelength_nm": null,
  "label": "from-remote"
}
```
- `unit` は `"Wavelength"` または `"Raman shift"`。
  `"Raman shift"` のときは `laser_wavelength_nm`が必須（無いと `422`）。
  逆に `"Wavelength"` のときは `laser_wavelength_nm` を設定できません（設定すると `422`）。
  axis_kindと励起波長を同時に持つ状態を作れないようにするためです。
- `c0`/`c1`/`c2`/`laser_wavelength_nm` は有限な数値である必要があります（`NaN`/`Infinity`は`422`）。
  `laser_wavelength_nm`を指定する場合は正の値である必要があります。
  このエンドポイントはconfiguration catalogを経由しないため、この検証はスキーマ自体で行われます。
- `label` は省略可（既定 `"api"`）。
  表示用のラベル文字列。
- `unit: "Raman shift"` の場合、`laser_wavelength_nm` は現在GUIの励起波長設定（0.001 nm解像度）と一致している必要があります。
  一致しない場合は物理的なレーザーを変えないままGUI側の励起波長表示だけを書き換えることになるため、`409 Conflict`（`code: "configuration_incompatible"`）で拒否されます。
  Configuration読み込みやCalibration画面の「Save and apply」は現在の励起波長設定をそのまま使うため、この検証には抵触しません。

**レスポンス例:**
```json
{"applied": true, "unit": "Wavelength", "c0": 694.2, "c1": 0.0153, "c2": 0.0, "label": "from-remote"}
```
