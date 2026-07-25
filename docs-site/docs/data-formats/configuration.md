---
sidebar_position: 6
title: Configurationファイル
description: grating・中心波長・ROI・横軸較正をバージョン管理付きで記録するJSONファイルとカタログの形式
---

# Configurationファイル

較正ダイアログの「Save and apply」を押すと、現在のgrating・中心波長・ROI・横軸較正がひとつの不変（immutable）レコードとして保存されます。
任意の保存先・ファイル名をその都度指定する必要はありません。
他のファイル形式と異なり、保存先は**OSのユーザー別application data領域**です。

- Windows: `%LOCALAPPDATA%\FluoraPressee\configurations`
- macOS: `~/Library/Application Support/FluoraPressee/configurations`
- Linux: `$XDG_DATA_HOME/FluoraPressee/configurations`（未設定なら `~/.local/share/...`）

この下に、正本となるJSONレコード群（`records/YYYY/MM/<configuration_id>.json`）と、検索用の`catalog.sqlite3` が置かれます。
一覧・検索は常にSQLiteカタログを使い、JSONを毎回全件読み込むことはありません。

## レコードの単位: slot と configuration

- **slot**: 「同じ装置・同じgrating・同じ目標中心波長・同じROI」という測定条件のまとまりです。
  `spectrometer_model` / `spectrometer_serial_number` / `camera_model` / `camera_serial_number` / `grating_index` / `grating_grooves_per_mm` / `target_center_wavelength_nm`（pm単位に丸めたもの）/ `roi_mode` / `roi_start` / `roi_end` の組み合わせが同じであれば同一の `slot_id` になります。
- **configuration**: 1回の「Save and apply」で作られる不変バージョンです。
  同じslotへ再度保存すると、新しい `configuration_id` がそのslotの `active` バージョンになり、直前のバージョンは`archived`（履歴）として残ります。
  履歴は自動削除されません。

## JSONレコードの構造

```json
{
    "schema_version": 2,
    "configuration_id": "cfg_3f1a2b7c4e9d4a6c8b0f1e2d3c4b5a69",
    "slot_id": "slot_9e8d7c6b5a4938271605f4e3d2c1b0a9",
    "calibration_profile_id": "calprof_1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d",
    "created_at": "2026-07-22T15:30:00.123+09:00",
    "compatibility": {
        "spectrometer_model": "SP-2750",
        "spectrometer_serial_number": "SPEC-001",
        "camera_model": "DU-401",
        "camera_serial_number": "CAM-001"
    },
    "spectrometer": {
        "grating_index": 2,
        "grating_grooves_per_mm": 1200,
        "target_center_wavelength_nm": 694.0,
        "actual_center_wavelength_nm": 693.9998
    },
    "detector": {
        "roi_mode": "1d_roi",
        "roi_start": 113,
        "roi_end": 125,
        "detector_width": 1340,
        "detector_height": 400
    },
    "calibration": {
        "source": "emission_standard_polynomial",
        "unit": "Wavelength",
        "excitation_wavelength_nm": null,
        "reference_kind": "emission_lines",
        "standards": [
            {"standard_id": "Ne-I", "display_name": "Ne I", "quantity": "wavelength_nm"}
        ],
        "coefficients": {
            "c0": 673.3405851432854,
            "c1": 0.020990883361968825,
            "c2": -2.889725985123467e-07
        }
    }
}
```

- `configuration_id` は特定の1バージョンを指す不変ID、`slot_id` は同じgrating・中心波長・ROI条件を表すIDです。
  外部連携で条件を安定して参照したい場合は `slot_id` を、実行に使う正確なレコードを固定したい場合は `configuration_id` を使います。
  `calibration_profile_id` は同じslot内でaxis_kind（と、Raman shiftなら励起波長）ごとに独立したcalibrationのまとまりを指します。
- `target_center_wavelength_nm` は指令値（slot識別に使われる安定した値）、`actual_center_wavelength_nm`は移動後の実測値です。
- `detector.roi_mode` は `"1d_roi"` / `"1d_full"` / `"2d"` のいずれかです。
- `calibration.unit` が `"Raman shift"` の場合は `excitation_wavelength_nm` が必須です。
  `calibration.source`は較正の由来を表す文字列（GUIから登録される場合は常に `"emission_standard_polynomial"`）です。
- `calibration.reference_kind` は較正に使った標準の種類を表し、`"emission_lines"`（輝線標準、Wavelength較正）、`"emission_lines_with_excitation"`（輝線標準の波長を励起波長でRaman shiftへ変換した較正）、`"raman_standard"`（Raman標準物質のシフト値を直接使った較正）のいずれかです。
  `calibration.standards` は実際にピークを割り当てた標準（`calibrationStandards/` のカタログ由来、`standard_id` / `display_name` / `quantity`）の一覧で、手入力した値のみで較正した場合など標準を特定できない場合は空配列になります。
  これらは較正ダイアログの「Save and apply」でのみ設定され、この情報を持たない旧バージョンのレコードを読み込むと`unit`から推定した値が補われます。
- `compatibility` の各機器は、シリアル番号が取得できていればその完全一致を、取得できなければモデル名の完全一致を要求します。

## 保存されないもの

露光時間・積算数・試料/物質名・バックグラウンド・フィッティング条件は測定ごとに変わる値のため、Configurationレコードには含まれません。

## 整合性・削除

各JSONレコードにはSHA-256ハッシュがカタログ側に記録されており、読み込み時に検証されます。
ファイルが破損・改変されていた場合はエラーになります。
削除には2種類あります。

- 1つの**archived**（非active）バージョンのみを削除する操作。
  slotのactiveバージョンは削除できません。
- そのslotの全バージョン（activeを含む）とJSONファイルをまとめて削除する操作。

旧形式（バージョン管理前）の任意保存calibration JSONファイルはこのカタログにはimportされず、ファイル自体が削除されることもありませんが、Load Configuration画面の一覧には表示されません。

外部自動化からこれらのレコードを一覧・取得・適用する方法は[Configuration関連エンドポイント](../api/configurations.md)を参照してください。
