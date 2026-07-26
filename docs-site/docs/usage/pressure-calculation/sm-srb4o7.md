---
sidebar_position: 3
title: Sm2+:SrB4O7
description: Sm2+:SrB4O7 蛍光線による圧力スケールと温度補正
---

# Sm<sup>2+</sup>:SrB<sub>4</sub>O<sub>7</sub>

- 種類: 蛍光 (fluorescence)
- 横軸単位: nm
- ゼロ圧ピーク位置の初期値: 685.410 nm（あくまで初期値です。
  実際の測定系・試料で測ったゼロ圧位置に置き換えてください）

## 圧力スケール（4種類、いずれも温度補正は任意）

- **Datchi et al. 1997** — <i>J. Appl. Phys.</i>、0-0線、MXB1986ルビースケールに対して較正[DOI: 10.1063/1.365025](https://doi.org/10.1063/1.365025)

  $$
  P = C\,\Delta\lambda\,\frac{1 + a\,\Delta\lambda}{1 + b\,\Delta\lambda}
  $$

  $C = 4.032$ GPa/nm、$a = 9.29\times10^{-3}$ nm<sup>-1</sup>、$b = 2.32\times10^{-2}$ nm<sup>-1</sup>

- **Datchi et al. 2007** — <i>High Press. Res.</i>、0-0線、DO2007ルビースケールに対して較正[DOI: 10.1080/08957950701659593](https://doi.org/10.1080/08957950701659593)

  $$
  P = C\,\Delta\lambda\,\frac{1 + a\,\Delta\lambda}{1 + b\,\Delta\lambda}
  $$

  $C = 3.989$ GPa/nm、$a = 0.006915$ nm<sup>-1</sup>、$b = 0.0166$ nm<sup>-1</sup>

- **Rashchenko et al. 2015**（0-0線, λ1） — <i>J. Appl. Phys.</i> [DOI: 10.1063/1.4918304](https://doi.org/10.1063/1.4918304)

  $$
  P = \frac{A}{B}\left[\left(\frac{\lambda}{\lambda_0}\right)^{B} - 1\right]
  $$

  Mao et al. (1986) 型の式。
  $A = 2836$ GPa、$B = 14.3$

- **Wei et al. 2024**（0-0線, λ1、Arを圧媒体として使用） — <i>J. Appl. Phys.</i> [DOI: 10.1063/5.0178597](https://doi.org/10.1063/5.0178597)

  $$
  P = \frac{A}{B}\left[\left(\frac{\lambda}{\lambda_0}\right)^{B} - 1\right]
  $$

  Mao et al. (1986) 型の式。
  $A = 2761.0$ GPa、$B = -9.88$（58.6 GPaまでの範囲で較正）


## 温度シフト補正スケール（任意、2種類）

| スケール | 有効範囲 |
|---|---|
| Datchi et al. 2007 | 296 – 900 K |
| Wei et al. 2024（0-0線） | 296 – 923 K |

[圧力計算の共通の考え方](index.md#圧力計算の共通の考え方表記)のオフセット補正式$\lambda_0(T) = \lambda_{0,T_0} + [f(T)-f(T_0)]$ における $f(T)$ は次の通りです。

- **Datchi et al. 2007**

  $$
  f(T) = -8.7\times10^{-5}(T-296) + 4.62\times10^{-6}(T-296)^2 - 2.38\times10^{-9}(T-296)^3
  $$

- **Wei et al. 2024**（λ1の温度シフトに対する線形フィット、Table II）

  $$
  f(T) = a_1 (T-296),\quad a_1 = -0.70\times10^{-4}\ \text{nm/K}
  $$

