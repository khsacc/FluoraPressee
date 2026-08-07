---
sidebar_position: 4
title: フィッティング結果ファイル
description: ピークフィッティング・圧力計算の結果を保存するテキスト/CSVファイルの形式
---

# フィッティング結果ファイル

ピークフィッティングの結果は、単発測定と連続測定とで別々の形式・ファイルに保存されます。

## 単発測定: `*_fitting_results.txt`

「Save Data」でスペクトルデータファイルを保存する際、フィッティングがオンかつ「Save fitting results」チェックボックスがオンで、かつ有効なフィット結果がある場合に、同じフォルダへ`<データファイル名>_fitting_results.txt`（例: `data_20260722_153000_000_fitting_results.txt`）として併せて保存されます。
連続測定中の各フレーム保存でも同じ条件でこのファイルが1フレームごとに作成されます（連続測定全体をまとめた別ファイルについては下記「連続測定サマリ」を参照）。

ヘッダー行1行 + データ行1行のCSVです。

```
Function,R2,Peak1_Pos,Peak1_Err,Peak1_Width,Peak1_WErr,Baseline_Requested,Baseline_Selected,Baseline_b0,Baseline_b0_Err,Baseline_b1,Baseline_b1_Err,Baseline_b2,Baseline_b2_Err,Pressure_GPa,Pressure_Err_GPa,Scale,Sensor,Lambda0_nm
PseudoVoigt,0.998765,694.123456,0.000321,0.452000,0.001200,Linear,Linear,102.300000,0.015000,-0.005000,0.000210,nan,nan,12.345678,0.021000,Shen2020,Ruby,694.220000
```

- `Peak{i}_Pos` / `Peak{i}_Err` / `Peak{i}_Width` / `Peak{i}_WErr` の4列組が、実際にフィットしたピーク数（最大5）だけ並びます。
- `Baseline_Requested` / `Baseline_Selected` は、ユーザーが選んだベースライン次数（Auto Polynomialの場合はその選択結果を含む）です。
  `Baseline_b0/b1/b2` の3組は常に出力されますが、実際の次数より高い係数は未使用のため `nan` になります。
- `Pressure_GPa` 以降の列は、保存時に圧力計算ウィンドウが開いていて圧力が算出済みの場合のみ付加されます。
  `Lambda0_nm` は横軸が波長モードのとき、Raman shiftモードのときは代わりに `Nu0_cm-1` になります。

## 連続測定サマリ: `fitting_seq_summary_<開始日時>.txt`

連続測定の開始時にフィッティングがオンだった場合、実行全体をまとめた1つのCSVが選択ディレクトリ直下に`fitting_seq_summary_YYYYMMDD_HHMMSS.txt`（開始時刻ベース）として作成され、フレームが保存されるたびに1行ずつ追記されます。

```
# Fitting Function: PseudoVoigt
# Peak Count: 2
# Peak Sort: x descending
# Baseline Model: Linear
# Fitting Range: 690.0 to 698.0
Filename,Timestamp,Peak1 (nm),Peak1_Err (nm),Width1 (nm),Width1_Err (nm),Peak2 (nm),Peak2_Err (nm),Width2 (nm),Width2_Err (nm),R2,Pressure (GPa),Pressure_Err (GPa),Baseline Selected,Baseline b0,Baseline b1,Baseline b2
seq_00000_20260722_153005_012.txt,2026-07-22T15:30:05.012,694.123456,0.000321,0.452000,0.001200,696.456789,0.000410,0.480000,0.001500,0.998765,12.345678,0.021000,Linear,102.300000,-0.005000,nan
```

- 先頭5行はコメント行（`#`）で、実行全体に共通する条件（フィット関数、ピーク数、ピークソート順、ベースラインモデル、フィッティング範囲）を記録します。
  これらは実行開始時点の設定で固定され、実行中に変更しても更新されません。
- `Peak{i}` / `Width{i}` の単位（`(nm)` または `(cm-1)`）は、そのフレームの実際の較正状態ではなく、実行開始時点のRaman shift表示切り替えの状態で決まります。
- `Pressure (GPa)` / `Pressure_Err (GPa)` の2列は、連続測定の開始時点で圧力計算ウィンドウが表示されていた場合のみヘッダーに含まれます。
  個々の行への出力も各フレーム保存時点でのウィンドウの表示状態に応じて行われるため、実行中に圧力計算ウィンドウの開閉を切り替えると、行によって列数がヘッダーとずれる場合があります。
- そのフレームのフィッティングが失敗した場合、ピーク・幅・R2の列は数値ではなく文字列 `NaN` になります（単発測定側の未使用ベースライン係数が `nan`（小文字）になるのとは表記が異なります）。
