---
sidebar_position: 1
title: API連携
description: HTTP APIの起動方法・認証・排他制御とエンドポイント一覧
---

# API連携

FluoRaPressée は、同一LAN内の他のPCからHTTP経由で測定をトリガーできるAPIモードを持っています。
校正やROI設定などの基本操作はGUI内で完結させたうえで、ユーザーが明示的に「API Server」パネルの**Start API Server** ボタンを押した場合のみAPIサーバーが起動します。
APIサーバーが起動している間、GUIの測定・設定系の操作（取得ボタン、露光時間、校正、ROI、フィット設定など）はロックされ、プロットスタイルや自動レンジ調整などの表示系操作のみ引き続き行えます。
**Stop API Server** を押すとロックは解除され、ローカルでの操作権が戻ります。

## 起動方法

1. GUIで校正・ROI・分光器設定など、必要な準備をすべて済ませておきます。
2. 画面下部の「API Server」パネルでポート番号を指定し（既定 `8765`）、**Start API Server** を押します。
3. 状態表示ラベルに接続用URL（`http://<このPCのIP>:<port>/docs`）と API キー（`X-API-Key`）が表示されます。
   このキーはアプリ初回起動時に一度だけ生成され、`fluora_pressee_api_key.json`（リポジトリルート）に永続化されるため、**サーバーを再起動しても、アプリ自体を再起動しても同じ値のまま変わりません**。
   想定される利用形態は「特定の連携アプリケーションに事前にこのキーを設定しておき、Start API Server を押した瞬間からその連携アプリが認証済みで叩ける」というもので、毎回キーを読み上げて相手に伝える必要はありません。
4. `http://<このPCのIP>:<port>/docs` にブラウザでアクセスすると、Swagger UI で全エンドポイントの仕様をその場で確認・試行できます。
5. 使い終わったら **Stop API Server** を押します。
   ウィンドウを閉じる際にサーバーが起動したままでも自動的に停止されます。

## 認証

すべてのエンドポイント（`/docs`, `/openapi.json` を除く）は `X-API-Key` ヘッダーが必須です。
ヘッダーが無い場合・値が一致しない場合はいずれも `401 Unauthorized` を返します。

```
X-API-Key: <API Server パネルに表示されているキー>
```

キーが漏洩した場合や、連携先アプリを変更してキーを差し替えたい場合は、メニューバーの**API → Regenerate Key** から手動で再発行できます。
再発行すると**古いキーはその場で即座に無効化される**（サーバーを再起動する必要はありません）ため、連携アプリ側の設定も新しいキーに更新してください。
更新前に届いたリクエストは古いキーのままなら `401` になります。

## 排他制御

同時に実行できる取得は1つだけです（ローカルのGUI操作・他のAPIリクエストを含めて）。
取得中に別の取得リクエストが来た場合、後から来た方は `409 Conflict`（`{"detail": "acquisition busy"}`）を返します。
[`GET /hardware/camera?refresh=true` と `GET /hardware/spectrometer?refresh=true`](hardware.md) のライブ状態照会も同じ排他ゲートを使用します。
測定・校正・分光器移動・別のライブ照会と競合した場合は`409 Conflict`（`{"detail": "instrument busy"}`）を返します。
`refresh=false` のキャッシュ取得と[`GET /config`](config.md) はこの排他ゲートを使用しません。

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

エラーコード一覧は[こちら](errors.md)、curlでの実行例は[こちら](examples.md)を参照してください。
