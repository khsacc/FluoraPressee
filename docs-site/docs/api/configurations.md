---
sidebar_position: 5
title: Configuration関連エンドポイント
description: 保存済みconfigurationの一覧・取得・適用 (GET/POST /configurations)
---

# Configuration関連エンドポイント

## slotとcalibration profile

同じ物理条件（装置・grating・centre position・ROI = *slot*）でも、Wavelength校正とRaman shift校正（さらに異なる励起波長ごと）は互いに独立した*calibration profile*として同時にactiveであり得ます。
1つのslotに対して、Wavelengthのprofileが1つと、励起波長ごとのRaman shiftのprofileが複数、同時にactiveになれることがある、ということです。
新しい校正を保存しても、上書きされるのは同じaxis_kind・同じ励起波長のprofileだけで、他のprofileはarchiveされません。

## GET /configurations

GUIのLoad Configurationと同じcatalogから、configurationの軽量summaryを返します。
既定では接続中の装置と互換性がある各slotの、各profileごとのactive versionだけを返すため、同じ条件で校正を繰り返しても通常の選択肢は増えません。

- `active_only=true`: `false`にすると旧versionも含めます。
- `include_incompatible=false`: `true`にすると非互換configurationも理由付きで含めます。
- `limit=100`: 1～1000。
- `offset=0`: pagination offset。

応答には`catalog_revision`, `items`, `total`, `limit`, `offset`を含みます。
summaryにはgrating、centre position、ROI、calibration unitに加えて`axis_kind`（`"wavelength"` / `"raman_shift"`）と`excitation_wavelength_nm`（Raman shiftのみ、null以外）を含みます。
同じslotに複数のprofileがあるかどうかは`profile_count_for_slot`で分かります。
calibration係数自体は含みません。

## `GET /configurations/{configuration_id}`

immutableなconfiguration record全体を返します。
`configuration.calibration.coefficients`を使えば、pixel軸で取得したデータへクライアント側で後から校正を適用できます。
`configuration.calibration.raw_spectrum`には、この較正のピークをフィットした際の生スペクトル(生センサーpixel順の強度値配列)が含まれます(この情報を持たない旧バージョンのレコードでは`null`)。
現在の装置との互換性も返します。

## POST /configurations/resolve

ES等が保持するstableな`slot_id`を、実行に使うexactな`configuration_id`へ固定します。
`slot_ids`の各要素は、そのslotに現在activeなprofileが1つだけならbareな文字列のままで解決できます。
2つ以上activeなprofileがある場合（例: Wavelengthと複数のRaman shift profileが同居している場合）は、`axis_kind`（と、Raman shiftなら`excitation_wavelength_nm`）を指定して明示的にどのprofileかを示す必要があります。

```json
{
  "slot_ids": [
    "slot_690nm",
    {
      "slot_id": "slot_700nm",
      "axis_kind": "raman_shift",
      "excitation_wavelength_nm": 532.0
    }
  ]
}
```

```json
{
  "catalog_revision": 42,
  "resolved": [
    {"slot_id": "slot_690nm", "configuration_id": "cfg_exact_1"},
    {"slot_id": "slot_700nm", "configuration_id": "cfg_exact_3"}
  ]
}
```

bareな`slot_id`に対応するactive profileが2つ以上ある場合は`axis_kind`等を指定しない限り`409 Conflict`（`code: "ambiguous_configuration_profile"`）になります。

`axis_kind`と`excitation_wavelength_nm`の組み合わせは不完全・矛盾した状態を`422`で拒否します: `axis_kind: "raman_shift"`には有限かつ正の`excitation_wavelength_nm`が必須、`axis_kind: "wavelength"`や`axis_kind`省略時は`excitation_wavelength_nm`を設定できません（設定すると無視されるのではなく`422`エラーになります）。

ESはsequence検証時にresolveし、実行中は返されたexact IDを使用します。
これにより、検証後に同じslot/profileへ新しい校正が保存されても実行中のconfigurationは暗黙に変化しません。

## `POST /configurations/{configuration_id}/apply`

指定configurationのgrating、centre position、ROIと横軸状態を適用し、移動完了後に応答します。

```json
{"axis_mode": "calibrated"}
```

- `axis_mode="calibrated"`（既定）: 保存済みcalibrationを適用します。
- `axis_mode="pixel"`: grating・centre・ROIだけを適用し、横軸はpixelにします。

応答の`configuration.axis_mode`自体は`"pixel"` / `"native_wavelength"` / `"calibrated"`の3値を取りえます（Ocean Optics等、FluoraPressée較正なしで独自の波長軸を報告する機種向け。
詳細は[`POST /acquire`](acquire.md)の`x_axis`説明を参照）。
リクエストの`axis_mode`パラメータ自体は引き続き`"calibrated"`/`"pixel"`の2値のみを受け付けます。

`configuration.unit`は`x_axis.unit`とは別の語彙（"Wavelength" / "Raman shift" / "pixel"、[`POST /calibration`](calibration.md)の`unit`と同じ）を使います。
`axis_mode="native_wavelength"`の場合も、pixelではなく表示モードに応じて"Wavelength"または"Raman shift"を返します。

現在のgrating・centre・ROIが同じslotと一致している場合、装置移動は省略して横軸状態だけを更新します。
応答には`configuration`, `hardware_state`, `display_label`を含みます。
