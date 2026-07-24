---
sidebar_position: 7
title: Cubic BN TO
description: Cubic BN TOモードによる圧力スケールと温度補正
---

# Cubic BN TO

- 種類: Raman
- 横軸単位: cm⁻¹
- ゼロ圧ピーク位置の初期値: 1058.3 cm⁻¹（あくまで初期値です。実際の測定系・試料で測ったゼロ圧位置に
  置き換えてください）

## 圧力スケール（2種類）

- **Kawamoto et al. 2004** — <i>Rev. Sci. Instrum.</i>（線形、3.45 cm⁻¹/GPa、温度補正は任意）
  [DOI: 10.1063/1.1765756](https://doi.org/10.1063/1.1765756)

  $$
  P = \frac{\nu - \nu_0}{3.45}
  $$

- **Datchi et al. 2004** — <i>Phys. Rev. B</i>、温度依存項を式に内包、有効範囲 300 – 723 K、
  **温度補正は必須**
  [DOI: 10.1103/PhysRevB.69.144106](https://doi.org/10.1103/PhysRevB.69.144106)

  $$
  A(T) = c_0 + c_1 T + c_2 T^{2},\qquad B(T) = \nu_{0,T_0} + a\,T + b\,T^{2}
  $$
  $$
  P = -\frac{1}{2d}\left[A(T) + \sqrt{A(T)^{2} + 4d\,\big(\nu - B(T)\big)}\right]
  $$

  $c_0 = 3.07$、$c_1 = 1.25\times10^{-3}$、$c_2 = -1.03\times10^{-6}$、$a = -9.3\times10^{-3}$、
  $b = -1.54\times10^{-5}$、$d = -0.0103$。$B(T)$ はこのスケールの現在温度でのゼロ圧ピーク位置に
  相当し、「Zero-pressure peak at current T」欄に表示されます。

:::caution
Datchi et al. (2004) スケールを選択すると、温度補正は常時On（切替不可）になり、
「Temperature input is mandatory for this scale!」と表示されます。有効範囲は300 – 723 Kで、範囲外
だと赤字警告が出ます（計算は継続されます）。ただしこのスケールはReference T0が固定されないため、
基準温度の入力欄は編集可能なままです。
:::

## 温度シフト補正スケール（任意）

| スケール | 有効範囲 |
|---|---|
| Kawamoto et al. 2004 | 300 – 1000 K |

[圧力計算の共通の考え方](index.md#圧力計算の共通の考え方表記)のオフセット補正式
$\nu_0(T) = \nu_{0,T_0} + [f(T)-f(T_0)]$ における $f(T)$ は次の通りです。

$$
f(T) = a_0 + a_1 T + a_2 T^{2}
$$

$a_0 = 1060.6$、$a_1 = -0.010$、$a_2 = -1.42\times10^{-5}$

## 注意点

- Kawamoto et al. (2004) の温度シフト補正スケールは、同じKawamoto et al. (2004) の圧力スケールと
  組み合わせて使うことを想定しています。
- Datchi et al. (2004) スケールを使うと、「Zero-pressure peak at current T」欄に、計算に使われた
  現在温度でのゼロ圧ピーク位置が自動的に表示されます。
