---
sidebar_position: 2
title: インストール
description: FluoRaPresséeの動作環境とインストール手順
---

# インストール

本アプリケーションは開発中のため、現段階ではスタンドアロンで起動できるビルド版は配布しておりません。
折を見てビルド版をGitHubでリリースする予定です。

## 必須環境

- Windows 10またはWindows 11
- Python 3.9以上、3.13以下
- 使用する装置に対応したSDKまたはドライバー

## リポジトリの取得

```powershell
git clone https://github.com/khsacc/FluoRaPressee.git
cd FluoRaPressee
```

## Andor / Princeton Instruments

`setup.bat`を実行します。
プロジェクト内に`.venv`が作成され、必要なPythonパッケージがインストールされます。

使用する装置に応じて、次のランタイムもインストールしてください。

- Andor：Andor SDK（装置に付属のCD-ROMにインストーラーが入っている）
- Princeton Instruments：PICam Runtime（オンラインでダウンロード可能）

## Ocean Optics

Windowsでは`setup_oceanoptics.bat`を管理者として実行します。

macOSまたはLinuxでOcean Opticsを使用する場合は、次を実行します。

```bash
./setup_oceanoptics.sh
```

## 初回起動

Windowsでは`FluoRaPressee_run.bat`を実行します。
初回起動時に設定ウィザードが表示されるので、装置メーカー、接続情報、回折格子などを設定してください。

ウィザード完了後、装置設定はリポジトリルートの`spectrometerConfig.json`に保存されます。
ウィザードの各項目と設定ファイルの仕様については[ハードウェア設定](hardware-config.md)を参照してください。
