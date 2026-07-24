---
sidebar_position: 1
title: 圧力計算
description: 圧力計算ウィンドウで使えるセンサー・圧力スケール・温度補正の一覧と注意点
---

# 圧力計算

高圧実験で用いる各種センサー（蛍光・Ramanシフト）の測定ピーク位置から圧力を求める、「圧力計算ウィンドウ」
（メイン画面の「Open pressure calculator」ボタン、またはAnalysis Modeの右側パネル）についてまとめます。
具体的な計算式・係数は `src/core/pressureCalc.py` の `PressureCalculator` クラスに実装されており、
対応する文献情報の多くは[README_ja.md](https://github.com/khsacc/FluoRaPressee/blob/main/README_ja.md)の「圧力計算画面」の節にまとめられています。

## センサーの種類と対応する横軸

圧力計算ウィンドウで選べるセンサーは、横軸のモード（波長 nm / Raman shift cm⁻¹）によって以下のように分かれます。

- **波長 (nm) モード・蛍光センサー**: [Ruby](ruby.md)、[Sm²⁺:SrB₄O₇](sm-srb4o7.md)、[Sm²⁺:SrFCl](sm-srfcl.md)、
  [Sm³⁺:YAG](sm-yag.md)
- **Raman shift (cm⁻¹) モード・Ramanセンサー**: [¹³C diamond 1st order](diamond-13c.md)、
  [Cubic BN TO](cubic-bn.md)、[Zircon B1g](zircon.md)、[Quartz 464 cm⁻¹](quartz-464.md)、
  [Quartz 128 cm⁻¹](quartz-128.md)
- **Raman shift (cm⁻¹) モード・専用フィットが必要なセンサー**: [Diamond Raman Edge](diamond-raman-edge.md)

メインウィンドウ／Analysis Modeの横軸モードに応じて、選択できるセンサーが自動的に絞り込まれます。

## 圧力計算の共通の考え方（表記）

各センサーのページに載せている計算式は、すべて `src/core/pressureCalc.py` の `PressureCalculator`
クラスの実装をそのまま数式に書き下したものです。ページ間で共通して使う記法は次の通りです。

- 波長モードの蛍光センサー: $\lambda$ = 現在のピーク位置 (nm)、$\lambda_0$ = ゼロ圧ピーク位置 (nm)、
  $\Delta\lambda = \lambda - \lambda_0$
- Raman shiftモードのセンサー: $\nu$ = 現在のピーク位置 (cm⁻¹)、$\nu_0$ = ゼロ圧ピーク位置 (cm⁻¹)、
  $\Delta\nu = \nu - \nu_0$
- $T$ = Current T（現在温度、K）、$T_0$ = Reference T0（基準温度、K）
- 添字T0付きの $\lambda_{0,T_0}$ / $\nu_{0,T_0}$ は「基準温度 $T_0$ で実測したゼロ圧ピーク位置」、
  すなわちUIの Zero-pressure peak at T0 欄の値を指します

温度補正が**任意**なスケール（コード内で `temperature_mode = "none"`）をOnにした場合、ゼロ圧ピーク位置は
スケール固有のモデル関数 $f(T)$（多項式、またはDebyeモデルなどによる格子シフト量）を使って次のように
オフセット補正されます（`PressureCalculator.get_corrected_zero_peak`）。

$$
\lambda_0(T) = \lambda_{0,T_0} + \big[f(T) - f(T_0)\big]
$$

（Ramanセンサーでは $\lambda_0$ を $\nu_0$ に読み替えます。）$f(T)$ の絶対値そのものが正確でなくても、
$T_0$ での実測値 $\lambda_{0,T_0}$ を基準に差分だけを適用するため、相対的なシフト量さえ文献の値と
合っていれば正しく補正されます。具体的な $f(T)$ の形は各センサーのページに記載しています。

温度補正が**必須**なスケール（`temperature_mode = "embedded_pt"`）ではこのオフセット補正は行われず、
圧力の式 $P$ 自体が $T$ を直接の変数として含みます（式は各センサーのページを参照してください）。

## 共通の操作

1. **Sensor** と **Pressure Scale** を選びます。
2. **Zero-pressure peak position（ゼロ圧ピーク位置）** を入力します。センサーごとに文献の代表値が
   初期値として自動入力されますが、これはあくまで目安です。実際の測定系・試料で測ったゼロ圧位置
   （大気圧・室温での位置）を必ず自分で測定し、入力し直してください。
3. 現在のピーク位置はフィッティング結果から自動的に読み込まれます。複数ピークをフィットした場合は
   「Calculate pressure by」でどのピークを圧力計算に使うか選択します。
4. 必要に応じて温度補正を設定します（詳細は下記）。

## 温度補正の3パターン

センサー・圧力スケールの組み合わせによって、温度補正の扱いは次の3パターンのいずれかになります。

- **補正なし・補正手段もない**: ゼロ圧ピーク位置の温度シフトを表す補正式が用意されていません。
  「Temperature Correction」のOn/Offは切り替えられますが、Onにしても実質的に値は変化しません
  （あるいは温度補正欄自体が非表示になります）。
- **補正は任意**: 温度シフト補正スケールが用意されており、必要な場合のみOnにできます。On/Offの
  ラジオボタンで切り替え可能です。
- **補正が必須**: 圧力スケールの数式自体が現在温度を直接使います（コード内では
  `temperature_mode = "embedded_pt"`）。On/Offの切り替えUIは非表示になり常にOnとなり、
  「Temperature input is mandatory for this scale!」と赤字で警告表示されます。この種のスケールの
  一部は、基準温度T0も文献の基準値に固定され、変更できません（各センサーのページを参照）。

:::caution
現在温度が、選択した温度スケール（または温度補正必須スケールの有効範囲）の外にある場合、Current Tの
入力欄が赤くなり "Warning: T out of range" と表示されます。ただし計算そのものは止まらず、範囲外でも
外挿してそのまま圧力が計算されるため、警告が出た場合は文献の適用範囲を確認したうえで結果を解釈してください。
:::

## センサー一覧

| センサー | 種類 | 単位 | 圧力スケール数 | 温度補正 |
|---|---|---|---|---|
| [Ruby](ruby.md) | 蛍光 | nm | 8 | 任意（6スケールから選択） |
| [Sm²⁺:SrB₄O₇](sm-srb4o7.md) | 蛍光 | nm | 4 | 任意（2スケールから選択） |
| [Sm²⁺:SrFCl](sm-srfcl.md) | 蛍光 | nm | 3 | 任意（1スケール） |
| [Sm³⁺:YAG](sm-yag.md) | 蛍光 | nm | 1 | 任意（1スケール） |
| [¹³C diamond 1st order](diamond-13c.md) | Raman | cm⁻¹ | 2 | スケールにより異なる（必須・埋め込み／補正手段なし） |
| [Diamond Raman Edge](diamond-raman-edge.md) | Raman（専用フィット） | cm⁻¹ | 4 | 補正手段なし |
| [Cubic BN TO](cubic-bn.md) | Raman | cm⁻¹ | 2 | スケールにより異なる（必須／任意） |
| [Zircon B1g](zircon.md) | Raman | cm⁻¹ | 2 | 任意（2スケール、それぞれ対応する論文とペア） |
| [Quartz 464 cm⁻¹](quartz-464.md) | Raman | cm⁻¹ | 2 | 任意（1スケール） |
| [Quartz 128 cm⁻¹](quartz-128.md) | Raman | cm⁻¹ | 1 | 必須（埋め込み、T0固定） |
