---
sidebar_position: 2
title: Ruby
description: Cr3+:Al2O3（ルビー）R1蛍光線による圧力スケールと温度補正
---

# Ruby（Cr³⁺:Al₂O₃）

- 種類: 蛍光 (fluorescence)
- 横軸単位: nm（R1線の波長）
- ゼロ圧ピーク位置の初期値: 694.300 nm（あくまで初期値です。実際の測定系・試料で測ったゼロ圧位置に
  置き換えてください）

## 圧力スケール（8種類、いずれも温度補正は任意）

- **Shen et al. 2020** — <i>High Press. Res.</i> [DOI: 10.1080/08957959.2020.1791107](https://doi.org/10.1080/08957959.2020.1791107)

  $$
  P = A\,\frac{\Delta\lambda}{\lambda_0}\left(1 + B\,\frac{\Delta\lambda}{\lambda_0}\right)
  $$

  Kunc et al. (2003) 型の式。$A = 1870$ GPa、$B = 5.63$

- **Kraus et al. 2016** — <i>Phys. Rev. B</i> [DOI: 10.1103/PhysRevB.93.134105](https://doi.org/10.1103/PhysRevB.93.134105)

  $$
  P = \frac{A}{B}\left[\left(\frac{\lambda}{\lambda_0}\right)^{B} - 1\right]
  $$

  Mao et al. (1986) 型の式。$A = 1915.1$ GPa、$B = 10.603$

- **Sokolova et al. 2013** — <i>Russ. Geol. Geophys.</i> [DOI: 10.1016/j.rgg.2013.01.005](https://doi.org/10.1016/j.rgg.2013.01.005)

  $$
  P = A\,\frac{\Delta\lambda}{\lambda_0}\left(1 + B\,\frac{\Delta\lambda}{\lambda_0}\right)
  $$

  Kunc et al. (2003) 型の式。$A = 1870$ GPa、$B = 6.0$

- **Jacobsen et al. 2008** — <i>Am. Min.</i>、ヘリウム圧媒体用にMgOスケールへ較正
  [DOI: 10.2138/am.2008.2988](https://doi.org/10.2138/am.2008.2988)

  $$
  P = \frac{A}{B}\left[\left(\frac{\lambda}{\lambda_0}\right)^{B} - 1\right]
  $$

  Mao et al. (1986) 型の式。$A = 1904.0$ GPa、$B = 10.32$

- **Dorogokupets and Oganov 2007** — <i>Phys. Rev. B</i> [DOI: 10.1103/PhysRevB.75.024115](https://doi.org/10.1103/PhysRevB.75.024115)

  $$
  P = A\,\frac{\Delta\lambda}{\lambda_0}\left(1 + m\,\frac{\Delta\lambda}{\lambda_0}\right)
  $$

  $A = 1884$ GPa、$m = 5.5$

- **Holzapfel 2003** — <i>J. Appl. Phys.</i> [DOI: 10.1063/1.1525856](https://doi.org/10.1063/1.1525856)

  $$
  P = \frac{A}{B+C}\left\{\exp\left[\frac{B+C}{C}\left(1 - r^{-C}\right)\right] - 1\right\},\quad r = \frac{\lambda}{\lambda_0}
  $$

  $A = 1820$ GPa、$B = 14$、$C = 7.3$

- **Mao et al. 1986**（MXB1986） — <i>J. Geophys. Res.</i> [DOI: 10.1029/JB091iB05p04673](https://doi.org/10.1029/JB091iB05p04673)

  $$
  P = \frac{A}{B}\left[\left(\frac{\lambda}{\lambda_0}\right)^{B} - 1\right]
  $$

  Mao et al. (1986) 型の式。$A = 1904.0$ GPa、$B = 7.665$

- **Piermarini et al. 1975** — <i>J. Appl. Phys.</i>（線形、2.746 GPa/nm）
  [DOI: 10.1063/1.321957](https://doi.org/10.1063/1.321957)

  $$
  P = 2.746\,(\lambda - \lambda_0)
  $$

## 温度シフト補正スケール（任意、6種類）

| スケール | 有効範囲 |
|---|---|
| Kobayashi et al., unpublished | 0 – 300 K |
| Yen and Nicol 1992 | 0 – 600 K |
| Ragan et al. 1992 | 0 – 600 K |
| Datchi et al. 2007（高温域） | 296 – 900 K |
| Datchi et al. 2007（低温域） | 0 – 296 K |
| Wei et al. 2024（R1） | 296 – 773 K |

各スケールの $f(T)$（[圧力計算の共通の考え方](index.md#圧力計算の共通の考え方表記)のオフセット補正式
$\lambda_0(T) = \lambda_{0,T_0} + [f(T)-f(T_0)]$ に代入するモデル関数）は次の通りです。Kobayashi・
Yen and Nicolの2スケールは波数 $\tilde\nu = 10^7/\lambda$（cm⁻¹）の領域で補正を行い、最後に
$\lambda = 10^7/\tilde\nu$ でnmへ戻します。

- **Kobayashi et al., unpublished** — Debyeモデルによる格子シフト（$f(T)$ は $\tilde\nu$ の補正量）

  $$
  f(T) = \alpha \left(\frac{T}{\Theta}\right)^{4} \int_0^{\Theta/T} \frac{x^3}{e^x - 1}\,dx
  $$

  $\alpha = -458.9$ cm⁻¹、$\Theta = 794.0$ K

- **Yen and Nicol 1992** — Debyeモデルによる格子シフト（$f(T)$ は $\tilde\nu$ の補正量）

  $$
  f(T) = \alpha \left(\frac{T}{\Theta}\right)^{4} \int_0^{\Theta/T} \frac{x^3}{e^x - 1}\,dx
  $$

  $\alpha = -419$ cm⁻¹、$\Theta = 760$ K

- **Ragan et al. 1992** — 絶対波数モデル（$\tilde\nu$ 領域、単位 cm⁻¹）

  $$
  \tilde\nu(T) = 14423 + 4.49\times10^{-2}T - 4.81\times10^{-4}T^2 + 3.71\times10^{-7}T^3
  $$

- **Datchi et al. 2007（高温域）** — 絶対波長モデル（単位 nm、$T \geq 50$ K、296 – 900 K）

  $$
  f(T) = 694.281 + a_1 (T-296) + a_2 (T-296)^2 + a_3 (T-296)^3
  $$

  $a_1 = 0.00746$、$a_2 = -3.01\times10^{-6}$、$a_3 = 8.76\times10^{-9}$

- **Datchi et al. 2007（低温域）** — 絶対波長モデル（単位 nm、$T \geq 50$ K、0 – 296 K）

  $$
  f(T) = 694.281 + a_1 (T-296) + a_2 (T-296)^2 + a_3 (T-296)^3
  $$

  $a_1 = 0.00664$、$a_2 = 6.76\times10^{-6}$、$a_3 = -2.33\times10^{-8}$

- **Wei et al. 2024** — <i>J. Appl. Phys.</i>、R1線、296–773 Kの実測データに対する3次多項式フィット
  [DOI: 10.1063/5.0178597](https://doi.org/10.1063/5.0178597)（相対波長シフトモデル、単位 nm、296 – 773 K）

  $$
  f(T) = b_1 (T-296) + b_2 (T-296)^2 + b_3 (T-296)^3
  $$

  $b_1 = 7.29\times10^{-3}$、$b_2 = -3.47\times10^{-6}$、$b_3 = 1.09\times10^{-8}$

## 注意点

- 8種類の圧力スケールはすべて温度補正が任意です（数式自体は現在温度を必要としません）。温度補正を
  Offのままにする場合は、測定時の温度でのゼロ圧ピーク位置をそのまま「Zero-pressure peak」に入力して
  ください。
- 温度補正をOnにする場合は、Reference T0・Zero-pressure peak at T0を明示的に入力します。ルビーの
  場合T0は固定されないため、基準温度は自由に設定できます。
- ルビーのR1線はR2線と近接した二重線です。フィッティング設定のピーク数は2が推奨で、Rubyセンサーを
  選ぶとアプリが自動的に推奨値へ切り替えます。圧力計算に使うピーク（通常はR1線）は
  「Calculate pressure by」で選択してください。
- 選択した温度スケールの有効範囲外で計算すると赤字警告が表示されますが、計算自体は継続され外挿された
  値が返ります。
