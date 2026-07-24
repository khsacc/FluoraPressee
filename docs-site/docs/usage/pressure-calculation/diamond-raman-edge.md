---
sidebar_position: 6
title: Diamond Raman edge
description: ダイヤモンドアンビル自身の1次ラマンエッジ位置による圧力ゲージ
---

# Diamond Raman edge

- 種類: Raman（ダイヤモンドアンビル自身の高波数側ラマンエッジ位置を使う、専用の測定種別）
- 横軸単位: cm⁻¹
- ゼロ圧ピーク位置の初期値: 1334.0 cm⁻¹

:::caution
このセンサーを使うには、フィッティング設定の「Fit Function」で **Diamond Raman Edge** を選ぶ必要が
あります。これは通常のピーク関数フィットではなく、`-dI/dν` をPseudo-Voigtでフィットしてエッジ位置を
求める専用アルゴリズムで、ピーク数は常に1です。逆にFit Functionを Diamond Raman Edge にした場合は、
Pressure Scaleも本ページのDiamond Raman Edge系スケールのいずれかを選ぶ必要があります。組み合わせが
一致しないと圧力欄に "Configuration Error" と表示され、圧力は計算されません。
:::

## 圧力スケール（4種類）

- **Hilberer et al. 2026** — Hilberer, A., Loubeyre, P., Pépin, C. et al., "Spectroscopic limits of
  diamond anvils to 520 GPa and projected bandgap closure," <i>Nat. Commun.</i> 17, 2644 (2026).
  [DOI: 10.1038/s41467-026-69533-7](https://doi.org/10.1038/s41467-026-69533-7)

  $$
  P = k_0\,x\left[1 + \tfrac{1}{2}(k_0' - 1)\,x\right],\quad x = \frac{\nu}{\nu_0} - 1
  $$

  $\nu_0 = 1334.0$ cm⁻¹、$k_0 = 575.0(7.0)$、$k_0' = 3.3(0.1)$

- **Eremets et al. 2023** — Eremets, M.I., Minkov, V.S., Kong, P.P. et al., "Universal diamond edge
  Raman scale to 0.5 terapascal and implications for the metallization of hydrogen,"
  <i>Nat. Commun.</i> 14, 907 (2023).
  [DOI: 10.1038/s41467-023-36429-9](https://doi.org/10.1038/s41467-023-36429-9)

  $$
  P = a\,x + b\,x^{2},\quad x = \frac{\nu}{\nu_0} - 1
  $$

  $\nu_0 = 1332.5$ cm⁻¹、$a = 517.0$、$b = 764.0$

- **Akahama and Kawamura 2006** — Yuichi Akahama and Haruki Kawamura, "Pressure calibration of
  diamond anvil Raman gauge to 410 GPa," <i>J. Appl. Phys.</i> 100, 043516 (2006).
  [DOI: 10.1063/1.2335683](https://doi.org/10.1063/1.2335683)

  $$
  P = k_0\,x\left[1 + \tfrac{1}{2}(k_0' - 1)\,x\right],\quad x = \frac{\nu}{\nu_0} - 1
  $$

  $\nu_0 = 1334.0$ cm⁻¹、$k_0 = 547.0(11.0)$、$k_0' = 3.75(0.20)$

- **Akahama and Kawamura 2010** — Yuichi Akahama and Haruki Kawamura, "Pressure calibration of
  diamond anvil Raman gauge to 410 GPa," <i>J. Phys.: Conf. Ser.</i> 215, 012195 (2010), quadratic
  fit valid for P > 200 GPa (multimegabar range, up to 410 GPa).
  [DOI: 10.1088/1742-6596/215/1/012195](https://doi.org/10.1088/1742-6596/215/1/012195)

  他の3スケールと異なり、規格化シフト $x$ ではなくフィットで求めたエッジ位置 $\nu$ そのものの2次式です。

  $$
  P = c_0 + c_1\,\nu + c_2\,\nu^{2}
  $$

  $c_0 = 3141(3)$、$c_1 = -4.157(20)$、$c_2 = 1.429(12)\times10^{-3}$。$\nu_0 = 1334.0$ cm⁻¹は他
  スケールとの表示上の整合のために示しているだけで、この式自体には使われません。

| スケール | 基準エッジ位置 ν0 (cm⁻¹) | 式の形 |
|---|---|---|
| Hilberer et al. 2026 | 1334.0 | k0 = 575.0(7.0), k0' = 3.3(0.1) |
| Eremets et al. 2023 | 1332.5 | 2次式（a = 517.0, b = 764.0） |
| Akahama and Kawamura 2006 | 1334.0 | k0 = 547.0(11.0), k0' = 3.75(0.20) |
| Akahama and Kawamura 2010 | 1334.0（表示のみ、式には不使用） | 絶対波数ωの2次式：P = c0 + c1・ω + c2・ω²、c0 = 3141(3)、c1 = -4.157(20)、c2 = 1.429(12)×10⁻³ |

## 温度補正

このセンサーには温度補正の仕組み自体がありません。スケールを選ぶと「Temperature Correction」欄自体が
非表示になります。

## フィッティングの仕組み

ダイヤモンドのエッジは通常のピークとは異なり、強度が急激に落ち込む「段差（ステップ）」状の形状をして
いるため、他のセンサーのようにGauss/Lorentz/Pseudo Voigt/Moffatで強度スペクトルそのものをフィットする
ことはできません。代わりに `src/core/analysis.py` の `DataAnalyzer.fit_diamond_raman_edge` は、強度の
波数微分 $-dI/d\nu$ を計算して段差を微分空間の「ピーク」に変換し、そのピークをPseudo-Voigt関数で
フィットすることでエッジ位置を求めます。処理は次の順に行われます。

1. **等間隔グリッドへの補間**: フィット範囲内のデータを波数順に並べ替えて重複点を除いたうえで、同じ
   点数の等間隔グリッド（`np.linspace`）に線形補間（`np.interp`）します。数値微分には等間隔サンプリング
   が必要なためです。
2. **微分専用の平滑化**: 等間隔化した強度にSavitzky-Golayフィルタ（`scipy.signal.savgol_filter`）を
   かけます。この平滑化は数値微分のノイズを抑えるためだけに使い、表示される「フィット曲線」自体も
   この平滑化後の強度です（生の強度をそのままフィットしているわけではありません）。窓幅は
   `EDGE_SMOOTH_WIDTH`（2.0 cm⁻¹）をグリッド間隔で割った値を基準に、5点以上・`EDGE_MAX_SMOOTH_WINDOW`
   （51点）以下・奇数になるよう調整されます。
3. **数値微分とエッジ候補の探索**: `np.gradient` で微分し、符号を反転して $-dI/d\nu$ を得ます。
   Savitzky-Golayや数値微分はROI（フィット範囲）の端で精度が落ちるため、両端（窓幅の半分・最低2点・
   全点数の1/4を超えない範囲）を探索対象から除外したうえで $-dI/d\nu$ が最大になる点を、エッジ位置の
   初期値とします。これにより、ユーザーが指定したROIの端がダイヤモンドエッジ本体と誤認識されるのを
   防いでいます。
4. **エッジ近傍だけを切り出してフィット**: 上記の初期値を中心に ±`EDGE_LOCAL_HALF_WIDTH`（6.0 cm⁻¹）
   の範囲（最低12点。点数が足りなければ自動的に拡張）だけを切り出し、ROI全体ではなくこの局所領域だけ
   を実際のフィット対象にします。
5. **Pseudo-Voigt + 線形ベースラインでフィット**: 切り出した $-dI/d\nu$ を、Pseudo-Voigt関数（振幅・
   中心位置・FWHM・混合比η）と線形ベースラインの和でフィットします。

   $$
   -\frac{dI}{d\nu} \approx \mathrm{PV}(\nu;\,A,\,\nu_c,\,w,\,\eta) + b_0 + b_1\cdot\frac{\nu-\bar\nu}{\Delta\nu}
   $$

   $\bar\nu$ は切り出した局所領域の中心、$\Delta\nu$ はその幅で、線形項をこの規格化した変数で表す
   ことでフィットの数値的な安定性を確保しています。混合比ηの初期値は0.5（Gauss/Lorentz半々）です。
   フィットで得られる中心位置 $\nu_c$ がエッジ位置として採用され、その値と標準誤差がそのまま
   `PressureCalculator._calculate_diamond_edge`（上記各スケールの $\nu$）に渡されて圧力が計算されます。
6. **失敗時の扱い**: 共分散行列が推定できない場合（`scipy.optimize.OptimizeWarning`）や、点数不足・
   NaN混入などで例外が発生した場合は他のフィット関数と同様にフィット失敗として扱われ
   （"Fitting failed or out of range."と表示）、圧力は計算されません。

フィット結果パネルの表示も通常のピークフィットとは異なります。関数名の下は「Fit Peaks」やベースライン
選択ではなく "Method: -dI/dν, pseudo-Voigt + linear baseline" の1行のみで、ピーク名も「Diamond edge」
と表示されます（ピーク数は常に1、ベースラインは常に微分空間での線形1種類のみのため、選ぶ余地が
ありません）。グラフ上では、フィット曲線（平滑化後の強度スペクトル）に加えて、求めたエッジ位置に
縦線マーカーが表示されます。

## 注意点

- ゼロ圧位置(ν0)はスケールごとに固定されており、通常のセンサーとは異なりユーザーが入力・編集する欄
  （Zero-pressure peak）は無効化され、「Scale reference ν0」として表示のみになります。
- 上記の通り、Fit Function（Diamond Raman Edge）とPressure Scale（Diamond Raman Edge系）は必ず
  ペアで選択してください。
- **Akahama and Kawamura 2010** はP > 200 GPa（多メガバール領域、〜410 GPaまで）でのみ検証された式です。
  それより低圧側での適用は文献の対象範囲外であり、コード側にもこの圧力範囲を強制するチェックはないため、
  低圧のデータに使わないよう注意してください。
