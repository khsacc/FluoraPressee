---
sidebar_position: 4
title: Sm2+:SrFCl
description: Sm2+:SrFCl 蛍光線による圧力スケールと温度補正
---

# Sm²⁺:SrFCl

- 種類: 蛍光 (fluorescence)
- 横軸単位: nm
- ゼロ圧ピーク位置の初期値: 690.300 nm（あくまで初期値です。実際の測定系・試料で測ったゼロ圧位置に置き換えてください）

## 圧力スケール（3種類、いずれも温度補正は任意）

- **Lorenz et al. 1994** — <i>High Press. Res.</i>
  [DOI: 10.1080/08957959408203170](https://doi.org/10.1080/08957959408203170)

  $$
  P = \frac{A\lambda_0}{B}\left[\left(\frac{\lambda}{\lambda_0}\right)^{B} - 1\right]
  $$

  Mao et al. (1986) 型の式に、係数 $A$ を $\lambda_0$ 倍したものを使用。$A = 0.904$、
  $B = -11.6$（低圧側）または $B = -13.6$（高圧側）

- **Shen et al. 2021** — <i>High Press. Res.</i>（線形、1.123 nm/GPa）
  [DOI: 10.1080/08957959.2021.1931168](https://doi.org/10.1080/08957959.2021.1931168)

  $$
  P = \frac{\Delta\lambda}{C},\quad C = 1.123\ \text{nm/GPa}
  $$

- **Shen et al. 1991** — <i>High Press. Res.</i>（線形、1.10 nm/GPa）
  [DOI: 10.1080/08957959108245510](https://doi.org/10.1080/08957959108245510)

  $$
  P = \frac{\Delta\lambda}{C},\quad C = 1.10\ \text{nm/GPa}
  $$

:::note
Lorenz et al. (1994) スケールは、圧力が10 GPaを境に異なる指数 $B$ を用いる2つの領域式を組み合わせています。アプリは低圧側の式（$B=-11.6$）でまず計算し、結果が10 GPaを超える場合は自動的に高圧側の式（$B=-13.6$）に切り替えます。ユーザー側で何か操作する必要はありません。
:::

## 温度シフト補正スケール（任意）

| スケール | 有効範囲 |
|---|---|
| Lorenz et al. 1994（Debye型格子シフト＋一フォノン結合モデル） | 20 – 650 K |

[圧力計算の共通の考え方](index.md#圧力計算の共通の考え方表記)のオフセット補正式
$\lambda_0(T) = \lambda_{0,T_0} + [f(T)-f(T_0)]$ を波数 $\tilde\nu = 10^7/\lambda_0$（cm⁻¹）の領域で適用します。$f(T)$ はDebye型格子シフトと一フォノン結合シフトの和です。

$$
f(T) = \alpha\left(\frac{T}{\Theta_D}\right)^{4}\int_0^{\Theta_D/T}\frac{x^3}{e^x-1}\,dx
      \;+\; \beta\left(\frac{T}{T_e}\right)^{2}\,\mathrm{P.V.}\!\int_0^{\Theta_D/T}\frac{x^3}{(e^x-1)(x+c)}\,dx
$$

$\alpha = 97.0$ cm⁻¹、$\Theta_D = 538.0$ K、$\beta = 2.4$ cm⁻¹、$T_e = 412$ K、$c = T_e/\Theta_D$
（第2項は $x=c$ に極をもつコーシー主値積分）。補正後は $\lambda_0(T) = 10^7/\tilde\nu(T)$ でnmに戻します。

## 注意点

- 3種類の圧力スケールはすべて温度補正が任意です。
