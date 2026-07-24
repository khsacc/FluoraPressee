---
sidebar_position: 9
title: Quartz 464 cm-1
description: 石英464 cm-1モードによる圧力スケールと温度補正
---

# Quartz 464 cm⁻¹

- 種類: Raman
- 横軸単位: cm⁻¹
- ゼロ圧ピーク位置の初期値: 464.4 cm⁻¹（あくまで初期値です。実際の測定系・試料で測ったゼロ圧位置に
  置き換えてください）

## 圧力スケール（2種類、いずれも温度補正は任意）

- **Schmidt and Ziemann 2000**（2次式、~23℃付近） — <i>Am. Min.</i>、
  0 < Δν ≤ 20 cm⁻¹（~2.1 GPaまで）で有効
  [DOI: 10.2138/am-2000-11-1216](https://pubs.geoscienceworld.org/msa/ammin/article/85/11-12/1725/133600/In-situ-Raman-spectroscopy-of-quartz-A-pressure)

  $$
  P\,(\text{MPa}) = a\,x^{2} + b\,x,\qquad x = \nu - \nu_0
  $$

  $a = 0.36079$、$b = 110.86$。アプリは $P\,(\text{GPa}) = P\,(\text{MPa})/1000$ に変換して表示します。

- **Schmidt and Ziemann 2000**（線形近似、9 cm⁻¹/GPa） — <i>Am. Min.</i>、100 – 560℃で一定と報告
  [DOI: 10.2138/am-2000-11-1216](https://pubs.geoscienceworld.org/msa/ammin/article/85/11-12/1725/133600/In-situ-Raman-spectroscopy-of-quartz-A-pressure)

  $$
  P = \frac{\nu - \nu_0}{9.0}
  $$

:::caution
2つのPressure Scaleは同じ論文由来ですが、想定温度・圧力域が異なります。2次式は室温(~23℃)・低圧域
（Δν ≤ 20 cm⁻¹、およそ2.1 GPaまで）専用、線形近似は100 – 560℃の高温域専用です。実験条件に応じて
選び分けてください。
:::

## 温度シフト補正スケール（任意）

| スケール | 有効範囲 |
|---|---|
| Schmidt and Ziemann 2000 | 77.15 – 833.15 K（-196 – 560℃） |

[圧力計算の共通の考え方](index.md#圧力計算の共通の考え方表記)のオフセット補正式
$\nu_0(T) = \nu_{0,T_0} + [f(T)-f(T_0)]$ における $f(T)$ は次の通りです（$T_c$ = 摂氏温度 = $T - 273.15$）。

$$
f(T_c) = 2.50136\times10^{-11}T_c^{4} + 1.46454\times10^{-8}T_c^{3} - 1.801\times10^{-5}T_c^{2}
       - 0.01216\,T_c + 0.29
$$

## 注意点

- 2種類の圧力スケールはすべて温度補正が任意です。温度シフト補正スケールは-196 – 560℃という広い
  範囲をカバーしています。
