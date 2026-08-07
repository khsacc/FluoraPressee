---
sidebar_position: 10
title: エラーコードと既知の制限
description: HTTPエラーコード一覧とAPIの既知の制限事項
---

# エラーコードと既知の制限

## エラーコード一覧

| コード | 意味 |
|---|---|
| 400 | リクエストの内容が不正（`dark.data` の長さ不一致、2Dモードでのフィット要求など） |
| 401 | `X-API-Key` が無い、または一致しない |
| 404 | 指定configurationまたはslotが存在しない |
| 409 | 他の操作が進行中、configurationが装置と非互換、またはbareな`slot_id`に対応する calibration profileが2つ以上あり曖昧（`code: "ambiguous_configuration_profile"`、[Configuration関連エンドポイント](configurations.md)参照） |
| 422 | リクエストボディのバリデーションエラー（Pydantic）、または `dark.mode="reuse_loaded"` の設定ミスマッチ |
| 500 | 予期しないサーバーエラー |
| 504 | configuration適用、取得またはライブ状態照会がタイムアウトした |

## 既知の制限

- **リモートからの新規dark取得は未実装**: `dark.mode="reuse_loaded"`/`"provided"` はGUIで事前に取得・保存した（またはクライアント自身が用意した）背景データを使うだけで、APIから「今すぐdarkを撮り直す」ことはできません。
  励起光を物理的に遮断するシャッター制御が本アプリに無いため。
  将来シャッター制御が実装された段階で追加を検討します。
- APIサーバーの起動自体が（ポート使用中などの理由で）失敗した場合、GUI側には明示的なエラー表示は出ません。
  ステータスラベルのURLにアクセスできない場合は、ポート番号を変えて再試行してください。
