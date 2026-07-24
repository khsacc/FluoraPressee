---
sidebar_position: 10
title: Quartz 128 cm-1
description: 石英128 cm-1モードによる圧力スケールと温度補正
---

# Quartz 128 cm⁻¹

- 種類: Raman
- 横軸単位: cm⁻¹
- ゼロ圧ピーク位置の初期値: 127.9 cm⁻¹（あくまで初期値です。実際の測定系・試料で測ったゼロ圧位置に
  置き換えてください）

## 圧力スケール（1種類、温度補正は必須）

- **Li et al. 2025** — <i>Chem. Geol.</i>、温度依存項を式に内包、基準温度T0 = 296.15 K (23℃) に固定、
  有効範囲 296.15 – 973.15 K
  [DOI: 10.1016/j.chemgeo.2024.122558](https://doi.org/10.1016/j.chemgeo.2024.122558)

  論文のEq. 3は $P\,(\text{MPa})$ について線形な順方向モデルです（$T_c$ = 摂氏温度 = $T - 273.15$）。

  $$
  \Delta\omega_{128}(T_c, P) = A T_c^{4} + B T_c^{3} + C T_c^{2} + D T_c + (E + F T_c)\,P + G
  $$

  アプリはこれを $P$ について代数的に解いて計算します。

  $$
  P\,(\text{MPa}) = \frac{\Delta\omega_{128} - \text{baseline}(T_c)}{\text{slope}(T_c)}
  $$
  $$
  \text{baseline}(T_c) = A T_c^{4} + B T_c^{3} + C T_c^{2} + D T_c + G,\quad
  \text{slope}(T_c) = E + F T_c
  $$

  ここで $\Delta\omega_{128} = \nu - \nu_0$。$A = 1.20176\times10^{-10}$、$B = -1.64508\times10^{-7}$、
  $C = 2.0665\times10^{-5}$、$D = -0.02134$、$E = 0.00599$、$F = 1.60394\times10^{-5}$、
  $G = 0.48515$。アプリは $P\,(\text{GPa}) = P\,(\text{MPa})/1000$ に変換して表示します。

:::caution
このセンサーで選べる圧力スケールはLi et al. (2025)の1種類のみです。温度補正は常時On（切替不可）に
なり、「Temperature input is mandatory for this scale!」と表示されます。Reference T0は296.15 K
(23℃)に固定され変更できません。Current T（現在温度）の入力は必須で、有効範囲（296.15 – 973.15 K）
外だと赤字警告が出ます（計算は継続されます）。
:::

## 温度シフト補正スケール

温度依存項が圧力式自体に組み込まれているため、別途選択できる温度シフト補正スケールはありません。

## 注意点

- 圧力式はP (MPa)に対して線形なため、アプリ内部では代数的に解いてGPaへ変換しています。
