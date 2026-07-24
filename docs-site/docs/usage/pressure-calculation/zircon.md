---
sidebar_position: 8
title: Zircon B1g
description: Zircon B1gモードによる圧力スケールと温度補正
---

# Zircon B1g

- 種類: Raman
- 横軸単位: cm⁻¹
- ゼロ圧ピーク位置の初期値: 1008.6 cm⁻¹（あくまで初期値です。実際の測定系・試料で測ったゼロ圧位置に
  置き換えてください）

## 圧力スケール（2種類、いずれも温度補正は任意）

- **Schmidt et al. 2013** — <i>Am. Min.</i>（線形、5.69 cm⁻¹/GPa）
  [DOI: 10.2138/am.2013.4143](https://doi.org/10.2138/am.2013.4143)

  $$
  P = \frac{\nu - \nu_0}{5.69}
  $$

- **Takahashi et al. 2024** — <i>J. Raman Spectrosc.</i>（線形、5.48 cm⁻¹/GPa）
  [DOI: 10.1002/jrs.6663](https://doi.org/10.1002/jrs.6663)

  $$
  P = \frac{\nu - \nu_0}{5.48}
  $$

## 温度シフト補正スケール（任意）

| スケール | 有効範囲 |
|---|---|
| Schmidt et al. 2013 | 296 – 1223 K |
| Takahashi et al. 2024 | 294 – 1078 K |

[圧力計算の共通の考え方](index.md#圧力計算の共通の考え方表記)のオフセット補正式
$\nu_0(T) = \nu_{0,T_0} + [f(T)-f(T_0)]$ における $f(T)$ は次の通りです。

- **Schmidt et al. 2013**（$T_c$ = 摂氏温度 = $T - 273.15$）
  $$
  f(T_c) = 7.53\times10^{-9}T_c^{3} - 1.61\times10^{-5}T_c^{2} - 2.89\times10^{-2}T_c + 1008.9
  $$

- **Takahashi et al. 2024**（$T$ はK単位のまま使用）
  $$
  f(T) = 7.54\times10^{-9}T^{3} - 2.23\times10^{-5}T^{2} - 1.84\times10^{-2}T + 1015.44
  $$


## 注意点

- 2種類の圧力スケールはすべて温度補正が任意です。
