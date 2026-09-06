---
sidebar_position: 12
title: エラーコードと既知の制限
description: HTTPエラーコード一覧とAPIの既知の制限事項
---

# エラーコードと既知の制限

## エラーコード一覧

| コード | 意味 |
|---|---|
| 400 | リクエストの内容が不正（`dark.data` の長さ不一致、2Dモードでのフィット要求など） |
| 401 | `X-API-Key` が無い、またはどのクライアントのキーとも一致しない |
| 403 | キーは有効だが、要求元アドレスがそのクライアントの許可IPに含まれない（`code: "ip_not_allowed"`、[クライアントと認証](clients.md)参照） |
| 404 | 指定configurationまたはslotが存在しない |
| 409 | 他の操作が進行中（`code: "busy"`、`reason` で内訳を区別）、`expected_state_token` が現在の状態と一致しない（`code: "state_token_mismatch"`）、configurationが装置と非互換、またはbareな`slot_id`に対応する calibration profileが2つ以上あり曖昧（`code: "ambiguous_configuration_profile"`、[Configuration関連エンドポイント](configurations.md)参照） |
| 422 | リクエストボディのバリデーションエラー（Pydantic）、または `dark.mode="reuse_loaded"` の設定ミスマッチ |
| 500 | 予期しないサーバーエラー |
| 503 | カメラが初期化されていない（`code: "camera_not_ready"`）、またはサーバーが停止処理中で新規リクエストを受け付けていない（`code: "shutting_down"`、[動作モード](standby.md)参照） |
| 504 | configuration適用、取得またはライブ状態照会がタイムアウトした |

### 409 の `reason`

| `reason` | 意味 |
|---|---|
| `local_sequential_run` | Sequential測定の実行中 |
| `local_operator_action` | 操作者によるローカル操作の実行中 |
| `another_api_request` | 別のAPIリクエストが装置を使用中 |

## 既知の制限

- **リモートからの新規dark取得は未実装**: `dark.mode="reuse_loaded"`/`"provided"` はGUIで事前に取得・保存した（またはクライアント自身が用意した）背景データを使うだけで、APIから「今すぐdarkを撮り直す」ことはできません。
  励起光を物理的に遮断するシャッター制御が本アプリに無いため。
  将来シャッター制御が実装された段階で追加を検討します。
- **通信はTLSで保護されていません**: APIキーは同一ネットワークセグメント上で受動的に傍受されうる状態で流れます。
  信頼できるLAN内での運用を前提としてください（[動作モード](standby.md)の「セキュリティ上の前提」参照）。
- **実行中リクエストの強制キャンセルは未実装**: モードを Off に切り替えても、実行中の取得や分光器移動は
  最後まで完了します。移動中のグレーティングを止めた場合の挙動などの実機検証が必要なためです。
- **レート制限はありません**: 連続測定と同程度の頻度でリクエストが届くことを前提としているため、
  閾値を設けると正規のクライアントを弾くリスクの方が大きいと判断しています。排他ゲートによる
  直列化で十分としています。
