---
sidebar_position: 3
title: Sm2+:SrB4O7
description: Sm2+:SrB4O7 蛍光線による圧力スケールと温度補正
---

# Sm²⁺:SrB₄O₇

- 種類: 蛍光 (fluorescence)
- 横軸単位: nm
- ゼロ圧ピーク位置の初期値: 685.410 nm（あくまで初期値です。実際の測定系・試料で測ったゼロ圧位置に
  置き換えてください）

## 圧力スケール（4種類、いずれも温度補正は任意）

- **Datchi et al. 1997** — <i>J. Appl. Phys.</i>、0-0線、MXB1986ルビースケールに対して較正
  [DOI: 10.1063/1.365025](https://doi.org/10.1063/1.365025)

  $$
  P = C\,\Delta\lambda\,\frac{1 + a\,\Delta\lambda}{1 + b\,\Delta\lambda}
  $$

  $C = 4.032$ GPa/nm、$a = 9.29\times10^{-3}$ nm⁻¹、$b = 2.32\times10^{-2}$ nm⁻¹

- **Datchi et al. 2007** — <i>High Press. Res.</i>、0-0線、DO2007ルビースケールに対して較正
  [DOI: 10.1080/08957950701659593](https://doi.org/10.1080/08957950701659593)

  $$
  P = C\,\Delta\lambda\,\frac{1 + a\,\Delta\lambda}{1 + b\,\Delta\lambda}
  $$

  $C = 3.989$ GPa/nm、$a = 0.006915$ nm⁻¹、$b = 0.0166$ nm⁻¹

- **Rashchenko et al. 2015**（0-0線, λ1） — <i>J. Appl. Phys.</i>
  [DOI: 10.1063/1.4918304](https://doi.org/10.1063/1.4918304)

  $$
  P = \frac{A}{B}\left[\left(\frac{\lambda}{\lambda_0}\right)^{B} - 1\right]
  $$

  Mao et al. (1986) 型の式。$A = 2836$ GPa、$B = 14.3$

- **Wei et al. 2024**（0-0線, λ1、Arを圧媒体として使用） — <i>J. Appl. Phys.</i>
  [DOI: 10.1063/5.0178597](https://doi.org/10.1063/5.0178597)

  $$
  P = \frac{A}{B}\left[\left(\frac{\lambda}{\lambda_0}\right)^{B} - 1\right]
  $$

  Mao et al. (1986) 型の式。$A = 2761.0$ GPa、$B = -9.88$（58.6 GPaまでの範囲で較正）


## 温度シフト補正スケール（任意、2種類）

| スケール | 有効範囲 |
|---|---|
| Datchi et al. 2007 | 296 – 900 K |
| Wei et al. 2024（0-0線） | 296 – 923 K |

[圧力計算の共通の考え方](index.md#圧力計算の共通の考え方表記)のオフセット補正式
$\lambda_0(T) = \lambda_{0,T_0} + [f(T)-f(T_0)]$ における $f(T)$ は次の通りです。

- **Datchi et al. 2007**

  $$
  f(T) = -8.7\times10^{-5}(T-296) + 4.62\times10^{-6}(T-296)^2 - 2.38\times10^{-9}(T-296)^3
  $$

- **Wei et al. 2024**（λ1の温度シフトに対する線形フィット、Table II）

  $$
  f(T) = a_1 (T-296),\quad a_1 = -0.70\times10^{-4}\ \text{nm/K}
  $$

## 注意点

- 4種類の圧力スケールはすべて温度補正が任意です。
- Datchi et al. (1997)・(2007)の2スケールはいずれも別のルビースケール（それぞれMXB1986・DO2007）
  を基準に較正されています。ルビーで求めた圧力と比較する際は、どちらのルビースケールを基準にしたかに
  注意してください。
- Wei et al. (2024)のスケールはShen et al. (2020)のルビースケール（[Ruby](ruby.md)ページのEq. (2a)相当）
  を基準に、Ar圧媒体下（298Kの58.6 GPaまで）で較正されています。Datchi et al. (1997)/(2007)（He圧媒体）
  とは非静水圧効果の影響が異なるため、単純な比較には注意してください。
