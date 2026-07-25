---
sidebar_position: 9
title: "POST /acquire/pressure"
description: 取得・フィッティングに圧力算出を追加するエンドポイント
---

# POST /acquire/pressure

[`POST /acquire/fit`](acquire-fit.md) と同じボディに、圧力算出パラメータを追加します。

**追加フィールド:**
```json
{
  "sensor": "ruby",
  "pressure_scale": "ruby_shen_2020",
  "zero_pressure_peak": 694.30,
  "temperature_correction": {
    "enabled": true,
    "scale": "ruby_kobayashi_unpublished",
    "current_t": 300.0,
    "t0": 298.15,
    "zero_pressure_peak_at_t0": 694.30
  }
}
```
- `sensor`/`pressure_scale`: [圧力計算](../usage/pressure-calculation/index.md)に一覧されている圧力マーカー・圧力スケールに対応するkey。
- Diamond Raman Edge の場合は `sensor: "diamond_raman_edge"` と`diamond_edge_...` スケールを指定します。
  `fit_function: "Diamond Raman Edge"` と通常のピーク圧力スケール、または通常のピークフィットとDiamond Edgeスケールを組み合わせると測定開始前に422エラーになります。
  Edgeスケール固有の `nu0` を使うため、`zero_pressure_peak` の値は計算には使用されません（互換性のためフィールド自体は必須）。
- `zero_pressure_peak`: 温度補正を使わない場合のゼロ圧力ピーク位置。
- `temperature_correction` は省略可。
  `enabled: false`、または省略した場合は温度補正無しで`zero_pressure_peak` がそのまま使われます。
  `enabled: true` の場合のみ、`scale` に`TEMPERATURE_SCALES` の key を指定し、その温度スケールでゼロ圧力ピークを補正してから圧力を計算します。
  温度が有効範囲外でも計算自体は続行し、`temperature_warning` に警告メッセージが入ります。
- 圧力スケール側で `T0` が固定されている場合は、リクエスト中の `t0` よりそのスケールの固定値が優先されます。

**レスポンス**: [`POST /acquire/fit`](acquire-fit.md) のフィールドに `pressure_gpa`, `pressure_err_gpa`, `zero_pressure_peak_at_current_t`, `temperature_warning` を追加。
```json
"pressure_gpa": 16.91,
"pressure_err_gpa": 8.26,
"zero_pressure_peak_at_current_t": 694.312,
"temperature_warning": null
```
`zero_pressure_peak_at_current_t` は、現在温度でのゼロ圧ピークを明示的に計算できるスケールでのみ値が入り、そうでない場合は `null` になります。
フィットが失敗した場合は `pressure_gpa`/`pressure_err_gpa`/ `zero_pressure_peak_at_current_t`/`temperature_warning` はすべて `null` になります。
ダブルピークフィットの場合、圧力計算にはPeak1（較正済みx軸で値が小さい方の主ピーク）が使われます。
横軸がpixelの場合は圧力計算できないため`400 Bad Request`を返します。
また、`sensor`が期待する単位（nm/cm<sup>-1</sup>）と現在有効なcalibrationの軸単位が一致しない場合（例: Wavelength calibrationが有効な状態でRaman系sensorを指定した場合）も同様に`400 Bad Request`になります。
校正のaxis_kindと単位を暗黙に変換することはありません（[Configuration関連エンドポイント](configurations.md)参照）。
