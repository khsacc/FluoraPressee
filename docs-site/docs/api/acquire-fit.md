---
sidebar_position: 8
title: "POST /acquire/fit"
description: 取得したデータにピークフィッティングを追加するエンドポイント
---

# POST /acquire/fit

[`POST /acquire`](acquire.md) と同じボディに、フィッティングパラメータを追加します。

**追加フィールド:**
```json
{
  "fit_function": "Pseudo Voigt",
  "fit_peak_count": 2,
  "peak_sort_order": "x_desc",
  "baseline_model": "constant",
  "fit_range": {"start": 690.0, "end": 700.0}
}
```
- `fit_function`: `"Gauss"`, `"Lorentz"`, `"Pseudo Voigt"`, `"Moffat"`, `"Diamond Raman Edge"` のいずれか。
  Diamond Raman Edge は負の一次微分をpseudo-Voigt＋線形背景でフィットし、`fit_peak_count` は必ず `1` にします。
- `fit_peak_count`: フィットするピーク数。
  1～5、既定値は2。
- `peak_sort_order`: `"x_desc"`, `"x_asc"`, `"intensity_desc"`, `"intensity_asc"` のいずれか。
- `baseline_model`: `"constant"`, `"linear"`, `"quadratic"`, `"auto_polynomial"` のいずれか。
  省略時は `"constant"`。
  `auto_polynomial` はBICを用いて0～2次から保守的に選択します。
- `fit_range` は省略可（省略時は取得データ全域でフィット）。
- 2Dイメージモードで取得した場合、フィットは意味を持たないため `400 Bad Request` を返します。

**レスポンス**: [`POST /acquire`](acquire.md) のフィールドに `fit` を追加。
```json
"fit": {
  "success": true,
  "x_fit": [690.0, 690.5, ...],
  "y_fit": [1204.5, 1198.2, ...],
    "fit": {
      "is_double": true,
      "Peak1": 694.32, "Peak1_Err": 0.01, "Width1": 1.2, "Width1_Err": 0.05,
      "Peak2": 692.80, "Peak2_Err": 0.02, "Width2": 1.1, "Width2_Err": 0.06,
      "baseline": {
        "requested": "Constant", "selected": "Constant", "degree": 0,
        "basis": "chebyshev", "coefficients": [120.4],
        "coefficient_errors": [2.1], "x_min": 690.0, "x_max": 700.0
      },
      "y_baseline": [120.4, 120.4, ...],
      "R2": 0.998
    }
}
```
フィットが失敗した場合(データ点不足・範囲外など)は `"success": false`, `"fit": null` になります(HTTPステータスは200のまま — フィット失敗は取得自体の失敗ではないため)。

## 関連エンドポイント

- [`POST /acquire/pressure`](acquire-pressure.md): このフィット結果からさらに圧力を算出します。
