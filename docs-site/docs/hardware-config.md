---
sidebar_position: 3
title: ハードウェア設定
description: spectrometerConfig.jsonの仕様と、初回接続時にGUIのセットアップウィザードから自動生成される仕組み
---

# ハードウェア設定

FluoRaPressée は、制御する分光器・カメラに関する情報を `spectrometerConfig.json` と呼ばれるファイルに格納し、起動時に自動的に読み出すことで、装置との通信の初期化を行います。
ここには、メーカー、接続方法、grating構成、ROIの初期値、冷却設定などが含まれます。
FluoRaPressée は、装置との堅牢な通信を実現するため、接続された装置の情報を型番とシリアル番号を用いて識別し、同一の機器と判断できた場合に限り、過去の設定情報を適用する操作を行うように設計されています。


## 初回起動時の自動生成

`main.py` を実行した際（`--debug` モードでも同様です）、`spectrometerConfig.json` が存在しなければセットアップウィザードが自動的に開きます。
ウィザードは3ステップです。

1. **メーカー選択**: Andor / Princeton Instruments / Ocean Optics
2. **接続設定**: メーカーごとに異なる項目を入力します（[メーカー別の接続設定項目](data-formats/spectrometer-config.md#メーカー別の接続設定項目)を参照）。
   Andor・Princeton InstrumentsのDLL/Runtimeパス欄は、ウィザードが裏でよくあるインストール先を自動検索し、見つかった候補を一覧に出します（✓ = ファイルを発見、✗ = 未発見、– = 未チェック）。
   **Read parameters from connected hardware** ボタンを押すと、接続済みの実機から接続情報・grating構成・機種名/シリアル番号を読み取り、各欄に反映します。
   読み取りに失敗した項目があっても手入力にフォールバックするだけで、ウィザードが停止することはありません。
3. **Grating・検出器設定**: gratingのgrooves/mm（カンマ区切り）、`flip_x`（左右反転表示）、冷却器の目標温度初期値などを設定します。
   Princeton Instrumentsは指定したCOMポートに対して `?GRATINGS` 照会を試み、成功すればgrating欄を自動的に埋めます。
   Ocean Opticsは可動gratingも冷却器も持たない固定分光器のため、この画面ではgratingと冷却温度の項目が表示されません。

ウィザードを最後まで完了する（Finish）と、入力内容が `spectrometerConfig.json` としてリポジトリルートに書き込まれます。

:::caution
ウィザードを**キャンセル**すると、`spectrometerConfig.json` は作成されず、アプリケーションはそのまま終了します。
デフォルト設定が自動生成されることはありません。
ハードウェアなしでUIの動作確認だけを行いたい場合（`--debug` モード）でも、初回はウィザードの入力を完了させる必要があります。
:::

メーカー別の接続設定項目、ファイル内の各キーの意味、`grating`配列・`hardware_identity`の構造、メーカー別の設定例、設定の変更方法、読み込みに関する補足など、設定ファイル自体の詳しい仕様は[spectrometerConfig.jsonの仕様](data-formats/spectrometer-config.md)を参照してください。
