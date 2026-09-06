---
sidebar_position: 1
title: API連携
description: HTTP APIの起動方法・認証・排他制御とエンドポイント一覧
---

# API連携

FluoRaPressée は、同一LAN内の他のPCからHTTP経由で測定をトリガーできるAPIモードを持っています。
校正やROI設定などの基本操作はGUI内で完結させたうえで、「API Server」パネルの **Mode** で
待ち受けを有効にします。

モードは3つあり、GUIがロックされる範囲が異なります（詳細は[動作モード](standby.md)を参照）。

| モード | 待ち受け | GUIの測定・設定系コントロール |
|---|---|---|
| **Off**（既定） | しない | 常に操作できる |
| **Standby** | 常時 | APIリクエストが装置を操作している間だけロックされる |
| **Locked** | 常時 | サーバーが起動している間ずっとロックされる |

どのモードでも、プロットスタイルや自動レンジ調整などの表示系操作は常に行えます。

## 使い方

1. GUIで校正・ROI・分光器設定など、必要な準備をすべて済ませておきます。
2. 「API Server」パネルでポート番号（既定 `8765`）と待ち受けアドレスを指定し、**Mode** を
   Standby または Locked にします。
3. 状態表示ラベルに接続用URL（`http://<このPCのIP>:<port>`）が表示されます。
4. 連携アプリケーションに、**API → Manage Clients...** で発行したキーを設定します
   （[クライアントと認証](clients.md)）。
5. 使い終わったら Mode を Off に戻します。
   選んだモードは保存され、次回起動時にはハードウェア初期化の完了後に自動で待ち受けを再開します。

## 認証

すべてのエンドポイントは `X-API-Key` ヘッダーが必須です。
キーはクライアントごとに発行し、クライアント単位で許可IPを設定できます。
詳細は[クライアントと認証](clients.md)を参照してください。

```
X-API-Key: <クライアントに割り当てたキー>
```

FastAPI の対話的ドキュメント（`/docs`, `/openapi.json`）は認証を通らないため既定では無効です。
必要な場合のみパネルの **Expose /docs** で有効にできます。

## 排他制御

同時に装置を操作できるのは1つだけです（ローカルのGUI操作・他のAPIリクエストを含めて）。
競合した場合、後から来たリクエストは `409 Conflict` を返し、`detail.reason` で
`local_sequential_run` / `local_operator_action` / `another_api_request` を区別できます。

排他ゲートを使う操作: 取得系エンドポイント、[configurationの適用](configurations.md)、
[`POST /calibration`](calibration.md)、
[`GET /hardware/camera?refresh=true` と `GET /hardware/spectrometer?refresh=true`](hardware.md)の
ライブ状態照会。
`refresh=false` のキャッシュ取得と[`GET /config`](config.md) はこの排他ゲートを使用しません。

ローカル側では、測定・分光器移動・構成ロード・背景の取得/読み込み・ハードウェア設定ダイアログ・
Instrument Status の更新が同じゲートを取ります。

## 装置状態トークン

各レスポンスには `instrument_state_token` が含まれます。
これは**不透明な文字列**で、クライアントは中身を解釈せず**等値比較のみ**を行ってください。
グレーティング・中心波長・ROI・校正・背景・露光時間・積算数・EM gain・冷却温度設定など、
取得結果を変えうる状態が変化すると値が変わります。

[`POST /acquire`](acquire.md) 系のリクエストに `expected_state_token` を指定すると、
「自分が確認した状態のまま取得する」ことを保証できます。
一致しない場合は `409`（`code: "state_token_mismatch"`、detail に現在値を含む）を返します。
**指定は任意**で、省略した場合は従来どおり動作します。

## エンドポイント一覧

| エンドポイント | 説明 |
|---|---|
| [`GET /status`](status.md) | 現在の状態を返す |
| [`GET /hardware/camera` / `GET /hardware/spectrometer`](hardware.md) | 接続機器の識別情報・状態 |
| [`GET /config`](config.md) | 起動時設定と保存済み設定 |
| [`GET /configurations` ほか](configurations.md) | 保存済みconfigurationの一覧・取得・適用 |
| [`POST /calibration`（非推奨）](calibration.md) | 較正係数を直接適用する旧エンドポイント |
| [`POST /acquire`](acquire.md) | データを1回取得する |
| [`POST /acquire/fit`](acquire-fit.md) | 取得＋ピークフィッティング |
| [`POST /acquire/pressure`](acquire-pressure.md) | 取得＋フィッティング＋圧力算出 |

動作モードとGUIロックの詳細は[こちら](standby.md)、クライアント管理と認証は[こちら](clients.md)、
エラーコード一覧は[こちら](errors.md)、curlでの実行例は[こちら](examples.md)を参照してください。
