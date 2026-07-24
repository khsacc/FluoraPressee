---
sidebar_position: 2
title: APIメニュー
description: HTTP API認証キーを再発行するRegenerate Key
---

# APIメニュー

## Regenerate Key

**API → Regenerate Key** は、HTTP API（`X-API-Key`ヘッダー）の認証キーをその場で再発行します。

実行すると確認ダイアログが表示され、続行すると新しいキーがメッセージボックスに表示されます。

- **古いキーはその場で即座に無効化されます**（APIサーバーの再起動は不要です）。
- APIサーバーが起動中の場合、画面下部の「API Server」パネルの状態表示ラベルも新しいキーで
  即座に更新されます。
- 更新前に届いたリクエストは、古いキーのままだと `401 Unauthorized` になります。連携している
  クライアント側の設定も新しいキーに合わせて更新してください。

キーは `fluora_pressee_api_key.json`（リポジトリルート）に永続化され、次回以降のAPIサーバー起動・
アプリ再起動でもこの新しいキーが使われ続けます。認証やAPIサーバーの起動方法全体については
[API連携](../../api/index.md)を参照してください。
