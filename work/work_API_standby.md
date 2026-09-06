# API常時待ち受け(Standby)モード 実装計画

作成日: 2026-08-07
改訂: 2026-08-07(レビュー第1回の反映 — 排他制御・異常終了・state revision のP0級の穴を修正)

## 背景

実機でのAPI運用を通じて、**「使う前にGUIでAPIサーバーのon/offを手動で切り替えるのが面倒」**という
問題が実用上深刻であることが判明した。現状の `work_API.md` の設計では、APIサーバーは操作者が
"Start API Server" を明示的に押したときだけ起動し、起動中はGUIの測定系コントロールが丸ごと
ロックされる。このため「リモートから測定したい」たびに操作者が物理的にGUIの前に行く必要があり、
リモート測定という機能の目的を部分的に損なっている。

そこで、**手動トグルの仕様自体は残しつつ**、限られた信頼できるアプリケーションからは常時
API経由の通信を受け取れるモードを追加する。GUIのブロックは「API経由で実際に操作が行われて
いる間だけ」に限定する。

### 当初この案を却下した理由と、現在の状況

`work_API.md` の「API稼働中のGUI操作ロック」節では、リクエスト単位のロックを検討したうえで
却下している。理由は以下だった:

> リクエストとリクエストの合間に操作者がグレーティング・中心波長・ROI・校正・ロード済み背景
> などの「物理的/論理的な測定条件」をローカルから変更できてしまうと、リモート側が最初に確認
> した前提(校正済みの状態)が本人の知らないところで崩れる。

**この前提はその後の `ConfigurationCatalog` 導入によって大きく変わった。** 現在の
`POST /acquire` は `configuration_id` を受け取れ、構成の位置決め・校正適用と取得が1つの
`_acquisition_gate` 所有として実行される(`api_mixin.py` の `api_acquire()`)。つまり
`configuration_id` を渡すクライアントにとっては、各リクエストが自己完結して物理状態を
再確立するため、「合間に前提が崩れる」問題そのものが存在しない。

残るのは `configuration_id` を省略した場合(現在の機器状態を維持するモード)だが、これに
ついては本計画で **instrument state token** を導入して「崩れたことを検知可能にする」形で
対応する(必須化はしない、後述)。

### 運用上の前提(確認済み)

- クライアントは `/acquire` を連続して叩く使い方をする。**送信モデルは closed-loop
  (前リクエストの応答を受け取ってから次を送る)とする**(方針11)。
- **「4 Hz」は要求仕様ではなく経験値である。** 実機の連続測定の測定間隔が概ねその程度で
  あった、つまり「カメラからこの程度の頻度でデータが読み出せる」というハードウェア側の
  実測にすぎない。**クライアントが課す締切ではないため、「4 Hzを維持できること」を
  受け入れ基準にしてはならない**(方針11・「オープンな課題」参照)。
- `configuration_id` は取り扱いが面倒なため **任意のまま**とする。必須化しない。
- 信頼できるアプリケーションの判定には **名前付き鍵**を導入する(現行の単一鍵では不十分、後述)。

### レビュー第1回で判明した、当初計画の誤り

本計画の初版は「API操作はすべて `_acquisition_gate` を通る」「BG取得・保存は既にゲートを
保持している」という前提に立っていたが、**いずれも成立していなかった**。コードを再確認した
結果、以下が事実である。この節は、同じ誤りを繰り返さないための記録として残す。

1. `POST /calibration` → `api_apply_calibration()` は**ゲートを取らない**
   (`server.py:277`, `api_mixin.py:731`)。
2. GUIの構成ロード `on_load_configuration()` は、ダイアログ確定後に
   `_prepare_configuration_for_loading()`(物理的な移動を伴う)を**ゲートなしで**呼ぶ
   (`file_io_mixin.py:348`)。
3. GUIの背景ロード `on_load_bg_clicked()` は**ゲートを取らない**(`file_io_mixin.py:647`)。
4. GUIの背景取得は `on_acq_bg_clicked()` でゲートを取るが、撮影完了時に
   `_process_completed_data()` の単発完了パスが**先にゲートを解放**し、その後に
   `_process_acquired_bg()` の保存ダイアログが開く。したがって保存ダイアログはゲート外である。
5. `ConfigWizard` は `check_and_create_config()` から、**メインウィンドウ生成前**に実行される
   (`app_bootstrap.py:16`)。メインウィンドウのゲートを持たせる対象ではない。
6. `_close_spectrometer_moving_dialog()` は `self.centralWidget().setEnabled(True)` を
   **無条件で**実行する(`spectrometer_control_mixin.py:215`)。API構成適用の完了時にも
   呼ばれるため、`api_active` ロック中に中央Widget全体が復活する。
7. `_api_start_acquire()` に**カメラ稼働確認がない**。また `api_acquire()` の
   `future.result(timeout=...)` に `finally` がなく、タイムアウト時にゲートと
   `_api_pending_future` が解放されない(`api_mixin.py:583`)。

## 採用する方針

### 方針1: 3状態モデル — 手動トグルの仕様を壊さない

APIサーバーの状態を、現行の on/off 2値から3状態に拡張する。

| モード | 待ち受け | GUI |
|---|---|---|
| `off` | しない | 自由(現行の停止時と同一) |
| `standby`(新規) | 常時(信頼済みクライアントのみ) | 自由。API操作中のみロック |
| `locked` | 常時 | 起動中ずっとロック(**現行の "Start API Server" と完全に同一の挙動**) |

`locked` は現行動作をそのまま保存したものであり、既存の運用手順・既存のドキュメントを
無効化しない。既定値は `off`(初回起動時)とし、操作者が明示的に `standby` を選んだ場合のみ
永続化して次回起動時に自動的に待ち受けを開始する。

### 方針2: 保護対象操作を先に列挙し、ゲートを単一チョークポイントにする

初版の「API操作はすべてゲートを通っている」という前提は誤りだった(背景の節を参照)。
**ゲートからUIロックを導出する設計は、ゲートの網羅性が前提条件である。** したがって、
まず保護対象操作の一覧を確定し、漏れを塞いでから所有者導出に進む(Step 0)。

保護対象操作一覧(Step 0 の成果物であり、以後この表を正とする):

| # | 操作 | 入口 | 現状 | 対応 | 完了の型 |
|---|---|---|---|---|---|
| 1 | GUI 単発取得 | `check_bg_and_take_single` (`file_io_mixin.py:61`) | 取得済 | 変更なし | 非同期 |
| 2 | GUI 連続取得 | `check_bg_and_start_meas` (`:68`) | 取得済 | 変更なし | 非同期 |
| 3 | GUI Sequential | `check_bg_and_start_seq` (`:75`) | 取得済 | 変更なし | 非同期 |
| 4 | GUI 分光器Apply | `spectrometer_control_mixin.py:143` | 取得済 | 変更なし | 非同期 |
| 5 | GUI 校正ダイアログ | `spectrometer_control_mixin.py:156` | 取得済 | 変更なし | 同期 |
| 6 | GUI 背景取得+保存 | `on_acq_bg_clicked` (`file_io_mixin.py:587`) | **部分的** — 撮影完了で解放され保存ダイアログはゲート外 | 保存完了までスコープ延長 | 非同期→同期 |
| 7 | GUI 背景ロード | `on_load_bg_clicked` (`file_io_mixin.py:647`) | **なし** | 追加 | 同期 |
| 8 | GUI 構成ロード | `on_load_configuration` (`file_io_mixin.py:348`) | **なし** | 追加(ダイアログ確定後・移動開始前に取得) | 非同期 |
| 9 | GUI ハードウェア設定ダイアログ | `src/ui/menu/` | **なし** | 追加 | 同期 |
| 10 | GUI Instrument Status 表示 | `src/ui/menu/` | **要調査** | Step 0で判定 | 同期 |
| 11 | API 取得 | `_api_start_acquire` (`api_mixin.py:259`) | 取得済 | `owner="api"` | 非同期 |
| 12 | API 構成適用 | `_api_start_configuration_apply` (`:357`) | 取得済 | `owner="api"` | 非同期 |
| 13 | API ハードウェア状態更新 | `_api_begin_hardware_refresh` (`:133`) | 取得済 | `owner="api"` | 非同期 |
| 14 | API 校正適用(deprecated) | `api_apply_calibration` (`:731`) | **なし** | 追加 `owner="api"` | 同期 |

**対象外(明示的に除外):**

- `ConfigWizard` — メインウィンドウ生成前に実行されるため(`app_bootstrap.py:16`)、
  メインウィンドウのゲートは存在しない。この時点ではAPIサーバーも起動していないので競合しない。
- `on_camera_initialized` / `on_camera_init_failed` の `centralWidget().setEnabled(True)`
  (`acquisition_mixin.py:295`, `:404`) — 初期化完了時のみ発火する。Standbyの自動待ち受け開始は
  **初期化完了後**に行うため(方針4)、ロック保持中にこれが走ることはない。この順序依存を
  崩さないこと。

### 方針3: ゲートAPIは「同期スコープ」と「非同期スコープ」を明確に分ける

上表の「完了の型」列が示す通り、保護対象操作は2種類ある。**この2つを同じAPIで扱おうとすると
必ず破綻する**ため、明示的に分離する。

- **同期スコープ**(ダイアログ、ファイルI/O、API校正適用): 開始と終了が同一の呼び出しの中で
  完結する。`contextmanager` によるスコープ式ゲート `acquisition_gate(owner)` を用い、
  **例外時も `finally` で必ず解放する**。
- **非同期スコープ**(取得、分光器移動): 開始と完了が別のイベントループターンに分かれる。
  context manager は使えない。明示的な取得/解放のままとし、代わりに**所有権の受け渡しと
  全解放経路を表として文書化**する(Step 0の成果物)。この種の操作こそ解放漏れの温床であり、
  「正常完了・失敗・タイムアウト・キャンセル・サーバー停止・アプリ終了」の6経路すべてに
  ついて解放地点を特定する。

### 方針4: UIロックは gate 所有者から導出する — ただしロック破り経路を先に塞ぐ

「API操作中」を新しく定義する必要はない。**ゲートをAPIが握っている区間が、そのまま
「API操作中」である。** `_try_acquire_gate()` に所有者を持たせ、`_release_acquisition_gate()`
で解除するという単一のチョークポイントからUIロックを導出する。

`_release_acquisition_gate()` の呼び出し箇所は現時点で12ヶ所あり、その多くがエラーパス・
タイムアウトパス・二重解放防止の防御的呼び出しである。ここに手作業で `_lock_ui`/`_unlock_ui`
を1対1で対応させると、**必ずどこかで解除漏れが発生し、GUIが永久にロックされたまま復帰しない**
という最悪の障害モードを招く。ゲートの取得/解放に完全に従属させることで、この種のバグを
構造的に排除する。

**ただしこの導出が成立するには、ロック状態を無視してウィジェットを再有効化する経路が
存在しないことが前提となる。** 現状、以下の3経路がこれを破る(Step 1で塞ぐ):

1. `_close_spectrometer_moving_dialog()` の `centralWidget().setEnabled(True)`
   (`spectrometer_control_mixin.py:215`) — **API構成適用の完了時に必ず通る経路**であり、
   最も影響が大きい。
2. `self.thread.exposure_set_finished.connect(lambda: self.spin_acq_time.setEnabled(True))`
   (`main_window.py:839`) — 無条件のラムダ。
3. `apply_roi_settings()` 内の `spin_vstart` / `spin_vend` / `radio_bg_on` の
   `setEnabled`(`acquisition_mixin.py:423-437`)。

`_lock_ui` を冪等化すると、いったん誤って再有効化された状態は**次のAPIリクエストでも
修復されない**(既にロック理由が立っているためウィジェット走査がスキップされる)。
したがって冪等化は上記3経路の修正とセットでなければならない。`_unlock_ui` も同様に
「そのreasonが実際に存在した場合のみ処理する」冪等化が必要。

**不変条件:** `_try_acquire_gate()` / `_release_acquisition_gate()` は必ずGUIスレッドから
呼ばれる。API側の呼び出しはいずれも `gui_bridge.call()` 経由でGUIスレッドに渡された関数の中で
行われており、この不変条件は現状すでに成立している。`_gate_owner` の読み書きにロックを
追加しないのはこの前提に依る。**新たにワーカースレッドから直接ゲートを取る経路を追加して
はならない。**

### 方針5: 解除は1秒のデバウンス、ロックは即時

連続測定相当の頻度(実測で概ね4 Hz程度)でリクエストが来るため、ゲート解放のたびに素直に
UIを再有効化すると、毎秒4回前後、約35個のウィジェットの enabled 状態がトグルすることに
なる。これは視覚的なちらつきとしてだけでなく、**操作者が編集中のスピンボックスから
フォーカスを奪う**という実害を生む。

そこでゲート解放から **1秒**(設定可能、既定1000ms)経過するまでUIロックを維持する。

**この値が closed-loop(方針11)と組み合わさると安全側に決まる点が重要である。** closed-loop
では次のリクエストまでの間隔が「クライアントの応答処理 + ネットワーク往復」だけであり、
取得時間に依存しない。したがってカメラが遅くなってもこの間隔は伸びず、連続実行中は
タイマーが一度も満了しない。バーストの間ロックは継続したままとなりちらつきは発生せず、
クライアントが停止した1秒後に解放される。

ロック側にデバウンスは掛けない(安全性のため即時ロックする)。

**デバウンスでは直らない問題への追加対応:** フォーカス喪失の被害は「ロック時」に発生する
ため、デバウンス(=解除の遅延)は発生頻度を下げるだけで根治しない。ロック適用時に
`QApplication.focusWidget()` を記録し、解除時に復帰させる処理を併せて入れる。

**計測起点はゲート解放時**とすること。リクエスト到着時を起点にすると、長時間の積算取得中に
タイマーが満了してしまう。

### 方針6: サーバー停止は「新規受付の停止」であり、in-flight操作の中断ではない

uvicorn の `should_exit = True` は実行中の同期ハンドラを中断しない。現在の `stop_api_server()`
は `should_exit` の設定と5秒の `join()` だけであり(`api_mixin.py:801-807`)、取得が5秒を
超えると**スレッドが生きたまま `self._api_server` / `self._api_server_thread` の参照だけが
消える**。その後、生き残ったリクエストがゲートを解放したりFutureを触ったりする。

そこで停止セマンティクスを次に確定する:

> **Offへの切替は「新規リクエストの受付を止める」ことを意味し、in-flight の操作は安全に
> 完了させる。ハードウェア操作の強制中断は行わない。**

明示的なキャンセル機構(取得中のカメラを止め、Futureを畳み、ゲートを解放する)を実装する案も
あるが、実機での安全性検証(移動中のグレーティングを止めた場合の挙動など)が必要であり、
本計画のスコープでは前者を採る。初版の「in-flightリクエストは失敗する」という記述は
実装不可能であったため撤回する。

### 方針7: 名前付き鍵 — 現行の単一鍵では「信頼できるアプリ」を表現できない

現行の鍵実装が証明しているのは「この要求元は秘密を知っている」ことだけで、「これは許可した
N個のアプリケーションのうちのどれか」ではない。具体的なギャップ:

1. **鍵が単一で、クライアントの区別がない。** 全クライアントが同一の principal であり、
   1つのクライアントだけを失効させることができない。`regenerate_api_key()` は全員を一斉に
   無効化する(`api_mixin.py:821-830`)。
2. **TLSなし。** `uvicorn.Config` に ssl 指定がなく(`api_mixin.py:795`)平文HTTPである。
   常時待ち受けにすると鍵が終日LAN上を流れ続けることになる。
3. **どのクライアントが叩いたかの記録が残らない。** `src/api/` にミドルウェアも
   `request.client` の参照も存在しない。
4. **鍵ファイルがリポジトリ直下の平文JSON**(`fluora_pressee_api_key.json`)であり、
   原子的保存でも権限制限でもない。
5. `/docs` と `/openapi.json` が認証を通っていない。`verify_api_key` は router の依存性
   (`server.py:81`)であり、app レベルの自動ドキュメントには適用されていない。
6. 鍵比較が `!=` で定数時間でない(`server.py:78`)。

研究室LANで現実的に起きるのは悪意ある攻撃ではなく**事故**である。同じ鍵を複数台のクライアント
設定にコピペした結果、A号機のスクリプトがB号機を叩く、止め忘れた古いクライアントが動き続ける、
といったケースが典型。常時待ち受けにするとこれが起きる窓が終日開く。

対応として、**名前付き鍵 + クライアント単位のIPアロウリスト**を導入し、鍵ファイルを
アプリケーションデータディレクトリへ移す。副次的に「どのクライアントが測定を叩いたか」の
記録が残るため、実験記録としての価値もある。

**TLSは導入しない。** 自己署名証明書の配布とクライアント設定のコストに対して、LAN内運用での
見返りが薄い。ただしこの判断を採る以上、**鍵は同一セグメント上で受動的に傍受可能なままで
ある**点を明示的に受容する。ドキュメントにも記載する。

### 方針8: state token は不透明な文字列とし、比較はゲート取得直後の一点に集約する

初版では「revision(整数)を `_api_start_acquire` 内で比較する」としたが、2つの欠陥があった。

1. **`configuration_id` との併用が成立しない。** 現在の `api_acquire()` は構成適用を完了して
   から `_api_start_acquire()` を呼ぶ(`api_mixin.py:562-575`)。構成適用自身が状態を変える
   ため、事前に取得した値を指定すると**常に不一致**になる。
2. **再起動をまたぐABA問題。** 起動ごとに0へ戻すと、同じ整数が別の状態を指す。

したがって:

- 比較地点を「**ゲートを取得した直後、状態を変える前**」の一点に集約する。
  `configuration_id` がある場合は `_api_start_configuration_apply()` 内で、無い場合は
  `_api_start_acquire()` 内で、いずれもゲート取得直後に同一のヘルパー
  `_assert_instrument_state_token(expected)` を呼ぶ。これにより
  `configuration_id` と `expected_state_token` の併用が「この構成を適用して取得したいが、
  その前に誰かが別の変更をしていたら中止したい」という意味で正しく成立する。
- 値は **不透明な文字列** `"<epoch>:<counter>"` とする(`epoch` はプロセス起動時に生成する
  UUID)。クライアントは中身を解釈せず等値比較のみ行う。これでABA問題は構造的に消える。
  フィールド名も意図が伝わるよう `instrument_state_token` とする。
- 応答へは `_acquire_response_payload()`(`server.py:134-150`)が**返却フィールドを明示列挙**
  しているため、`**state` に載せるだけでは出ない。ここにも明示的に追加する。

### 方針9: 応答stateは撮影完了時にゲート内でスナップショットする

現在の `api_acquire()` は、撮影完了(=ゲート解放済み)後にワーカースレッドから
`_api_finalize_acquire()` と `_api_configuration_state()` を別々の `gui_bridge.call()` で
読む(`api_mixin.py:587-596`)。複数クライアント運用では**この隙間に次のAPIリクエストが状態を
変更できる**ため、返された `hardware_state` / token が実際に取得したデータと一致しない
可能性がある。

そこで `_process_completed_data()` の中で、**ゲートを解放する前に**応答に必要な state を
まとめてスナップショットし、Futureのペイロードに載せる。`api_acquire()` は追加の
`gui_bridge.call()` をやめてペイロードから読む。

これは正しさの修正であると同時に、**`/acquire` あたりの `gui_bridge.call()` 往復を3回から
1回へ削減する**ため、連続実行時のスループット改善にもなる(方針11)。

### 方針10: 連続実行で顕在化する既存の性能問題を同時に解消する

`_api_configuration_state()` は `positioned_configuration_id` が設定されていると毎回
`configuration_catalog.get_configuration()` を呼ぶ(`api_mixin.py:449-451`)。その中身は
**SQLiteクエリ → JSONファイル読み込み → SHA-256による整合性検証**である
(`configuration_catalog.py:812-831`)。方針9のスナップショットはこれをGUIスレッドの
ゲート保持区間で行うことになるため、キャッシュは必須である。

`ConfigurationCatalog` のレコードは immutable なのでメモリキャッシュを安全に導入できる。
**ただしキャッシュはAPIワーカースレッドからも読まれるため `threading.Lock` で保護すること。**
また初回読込以降SHA-256検証が行われなくなるが、これは「このプロセスが catalog の唯一の
書き手である」という既存の前提と整合するトレードオフとして明示的に受容する。

### 方針11: 送信モデルは closed-loop とし、性能は絶対値でなく相対値で評価する

**送信モデルを closed-loop(前リクエストの応答を受け取ってから次を送る)に確定する。**
クライアント側の実装は変更可能であり、両案のうち closed-loop の方が負荷が小さいため。

- 実装が単純である。固定周期はスケジューラと、遅延したリクエストをスキップするか
  キューに積むかの判断が要る。closed-loop は応答を待つループを書くだけで済む。
- **クライアントが自分自身と衝突しない。** 前の取得が完了してから次を送るので、
  自己重複による409が構造的に発生しない。固定周期では取得が周期を超えた瞬間に409が出て、
  クライアント側に409のリトライ処理が必要になる。これは純粋な追加負担で見返りがない。
- 受け入れ基準が明快になる(「全リクエストが200」)。

**性能の評価基準も併せて確定する。** 「4 Hz」はクライアントが課す締切ではなく、実機の
連続測定の測定間隔が概ねその程度だったというハードウェア側の実測値にすぎない。したがって
「4 Hzを維持できること」は受け入れ基準として意味を持たない — カメラが遅ければAPIも当然
遅くなるだけである。

代わりに次を基準とする:

> **同一の露光時間・積算数において、API経由の連続取得のスループットが、GUIのローカル連続
> 測定のスループットと比べて著しく劣化しないこと。**

これは絶対値と違い、カメラの速度に依存せず「APIレイヤーが上乗せしているオーバーヘッド」
だけを測れる。closed-loop では1フレームあたりの所要時間が
`カメラ読み出し + APIオーバーヘッド` になるため、劣化幅がそのままオーバーヘッドを表す。
方針9の往復削減(3回→1回)と方針10のキャッシュは、この劣化幅を縮めるための施策と位置づける。

## 対象ファイル

**変更:**

```
src/ui/main_window.py                           # _gate_owner/token/タイマー初期化、APIパネル刷新、メニュー、839行のlambda修正
src/ui/ui_mixins/acquisition_mixin.py           # スコープ式ゲート、_try_acquire_gate(owner)、apply_roi_settings分割
src/ui/ui_mixins/sequential_mixin.py            # _lock_ui/_unlock_ui 冪等化、set_ui_enabled_during_seq(reapply_hardware)
src/ui/ui_mixins/api_mixin.py                   # モード管理、デバウンス、名前付き鍵、異常系清掃、スナップショット
src/ui/ui_mixins/config_mixin.py                # 鍵ファイルv2の読み書き・移行・原子的保存
src/ui/ui_mixins/file_io_mixin.py               # 背景ロード/構成ロード/背景保存のゲート、token更新地点
src/ui/ui_mixins/spectrometer_control_mixin.py  # _close_spectrometer_moving_dialog のロック考慮、token更新地点
src/ui/menu/                                    # ハードウェア設定・Instrument Status ダイアログのゲート
src/ui/local_cache.py                           # api_mode/api_port/api_bind_host/api_unlock_delay_ms/api_expose_docs
src/api/server.py                               # verify_api_key刷新、docs無効化、409/503 detail、応答フィールド追加
src/api/schemas.py                              # instrument_state_token / expected_state_token
src/core/configuration_catalog.py               # レコードのメモリキャッシュ(ロック付き)
```

**新規:**

```
src/ui/menu/api_clients_dialog.py               # クライアント管理ダイアログ
tests/test_api_clients.py                       # 鍵照合・IP照合・v1移行・原子的保存
tests/test_ui_lock_reasons.py                   # ゲート所有者からのロック導出、ロック破り経路の回帰
tests/test_api_acquire_failures.py              # タイムアウト清掃・カメラ未起動503・遅延フレーム
tests/test_instrument_state_token.py            # token生成・比較地点・configuration_id併用
```

## ステップ間の依存関係

**Step 0(ゲート監査)と Step 1(ロック破り経路の是正)は、他のすべてに先行する絶対条件である。**
この2つが完了するまで Step 2 以降に着手してはならない。初版の計画はこの2つを欠いていたため
実装開始できる状態になかった。

Step 2(所有者導出)は Step 0・1 に依存。Step 3(異常系)は Step 2 に依存(解放がロック解除を
伴うようになるため)。Step 4(デバウンス)は Step 2 に依存。Step 5(Standby本体)は Step 2・3・4
すべてに依存する。

Step 6(名前付き鍵)は Step 0-5 と**独立**しており並行可能。単体でも価値がある(現行の `locked`
運用でもIPアロウリストと `/docs` 閉塞は有効)。

Step 7 のうち構成レコードキャッシュは独立だが、**方針9のスナップショットがキャッシュに依存
する**ため、Step 3 より前に入れておくのが望ましい。token 部分は Step 5 の後。

推奨実装順序: **Step 0 → Step 1 → Step 2 → Step 7a(キャッシュのみ) → Step 3 → Step 4 →
Step 5 → Step 6 → Step 7b(token) → Step 8**

## 各ステップ共通の検証手順

1. `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v` が既存分含めて通ること。
2. `python main.py --debug` で例外なく起動し、"Camera Ready" になることを確認する。
3. `git diff --stat` で意図したファイルだけが変更されていることを確認する。
4. **回帰の要:** 各Stepの後で「`locked` モード(現行の Start API Server 相当)の挙動が
   一切変わっていないこと」を確認する。既存運用を壊さないことが本計画の絶対条件である。
5. **ゲート解放の回帰:** 各Stepの後で、ローカルの単発測定・連続測定・Sequential測定・
   分光器Apply・背景取得/ロード・構成ロードを一巡し、いずれの後も
   `_acquisition_gate.locked()` が False に戻っていることを確認する。
6. 実機が無い状態では `--debug` 確認のみで進め、実機確認が必要な項目は各Stepに
   `TODO(実機確認待ち)` として明記する。

### 連続実行スクリプト(Step 4以降で使用)

`scripts/_dev_api_load.py`(一時スクリプト、コミット任意)を用意する。方針11に従い
**closed-loop の1モードのみ**でよい: 前リクエストの応答受信後、待ち時間ゼロで次を送るループを
60秒間回し、達成レート(件/秒)と各リクエストの所要時間の分布を出力する。

受け入れ基準:

- **全リクエストが200であること**(closed-loop では自己重複による409は構造的に起きない)。
- UIロックが実行中ずっと継続し、ちらつかないこと。
- 停止から約1秒後にUIが復帰すること。
- 復帰時に、ロック前にフォーカスがあったウィジェットへフォーカスが戻ること。
- GUIスレッドが詰まっていないこと(プロット更新が止まらない、ウィンドウ移動に追随する)。
- 実行後に `_acquisition_gate.locked()` が False に戻っていること。

**スループットの評価は絶対値ではなく相対値で行う**(方針11)。同じ露光時間・積算数で
GUIのローカル連続測定を走らせたときのフレームレートを別途測り、API経由の達成レートと
比較する。差分がAPIレイヤーのオーバーヘッドである。`--debug` モードでも比較は可能だが、
合成スペクトル生成が実機の読み出しより速いためオーバーヘッドの比率は実機と異なる点に注意する
(絶対的な判定は実機で行う)。

---

## Step 0: 保護対象操作のゲート監査とスコープ式ゲートAPI

**優先度:** P0(他のすべてに先行)
**依存:** なし
**対象ファイル:** `src/ui/ui_mixins/acquisition_mixin.py`, `src/ui/ui_mixins/file_io_mixin.py`,
`src/ui/ui_mixins/api_mixin.py`, `src/ui/menu/`, `src/api/server.py`

**実行プロンプト:**

> 方針2の保護対象操作一覧に従い、ゲート未取得の経路を塞ぐ。**このStepではUIロックには一切
> 手を付けない**(排他制御の正しさだけを先に確立する)。
>
> 1. **スコープ式ゲートAPIの追加**(`acquisition_mixin.py`)。同期的に完結する操作用:
>    ```python
>    @contextmanager
>    def acquisition_gate(self, owner="gui"):
>        """同期スコープの排他ゲート。例外時も finally で必ず解放する。
>
>        取得や分光器移動のように「開始と完了が別のイベントループターンに分かれる」操作には
>        使えない(方針3)。それらは従来通り明示的な取得/解放を使うこと。
>        """
>        if not self._try_acquire_gate(owner):
>            raise GateBusyError(self._gate_busy_reason())
>        try:
>            yield
>        finally:
>            self._release_acquisition_gate()
>    ```
>    `GateBusyError` を `acquisition_mixin.py` に新設する。`_gate_busy_reason()` は Step 5 で
>    409の詳細化に使うため、ここでは現在の所有者を表す文字列を返す最小実装でよい。
>
> 2. **表#7 GUI背景ロード**(`file_io_mixin.py:647` `on_load_bg_clicked`)。
>    ファイルダイアログは**ゲートの外**に置き、ファイル選択後の読み込みと状態反映のみを
>    `with self.acquisition_gate():` で囲む。ダイアログを含めると、操作者がダイアログを
>    開いたまま放置している間ずっとAPIが409になる。取得失敗時は「他の操作が進行中」を
>    `QMessageBox` で表示する。
>
> 3. **表#8 GUI構成ロード**(`file_io_mixin.py:348` `on_load_configuration`)。
>    これは物理的な移動を伴う**非同期スコープ**である。`ConfigurationBrowserDialog` の
>    `exec()` はゲート外に置き、`Accepted` の後、`_prepare_configuration_for_loading()` を
>    呼ぶ**直前**に `_try_acquire_gate()` する。取得できなければ警告して中止する。
>    解放は API 側の `_api_start_configuration_apply` と同じ考え方で、
>    (a) 移動完了コールバック、(b) 例外による巻き戻し、(c) 移動のキャンセル
>    (`on_cancel_spectrometer_move`)の3経路すべてに置く。既存のAPI側実装
>    (`api_mixin.py:355-378`)を参考にすること。
>
> 4. **表#6 GUI背景取得の保存まで**。`on_acq_bg_clicked()` はゲートを取るが、
>    `_process_completed_data()` の単発完了パス(`acquisition_mixin.py:578` 付近)が
>    `_process_acquired_bg()` より先にゲートを解放するため、保存ダイアログはゲート外にある。
>    `_is_acquiring_bg` が True の場合は単発完了時にゲートを解放せず、`_process_acquired_bg()`
>    の**終了時**(保存成功・キャンセル・例外のすべて)に解放するよう変更する。
>    `try/finally` で囲むこと。現状の早期 `return`(データ無し・ファイル未選択)が
>    解放漏れにならないよう注意する。
>
> 5. **表#9/#10 メニューダイアログ**(`src/ui/menu/`)。ハードウェア設定ダイアログは
>    `with self.acquisition_gate():` で `exec()` ごと囲む(こちらは設定変更が目的であり、
>    開いている間の排他が正しい)。Instrument Status ダイアログについては、ライブの
>    ハードウェア問い合わせを行うかどうかをコードで確認し、行うなら同様に囲む。
>    行わない(キャッシュ表示のみ)なら対象外とし、その判断を作業ログに記載する。
>    取得失敗時は「リモート操作中のため開けません」と表示して開かない。
>
> 6. **表#14 API校正適用**(`server.py:277` `post_calibration` → `api_mixin.py:731`)。
>    `api_apply_calibration()` を `with self.acquisition_gate("api"):` で囲む。
>    このルートは deprecated だが**残っている以上は塞ぐ**。`GateBusyError` は
>    `server.py` で409に変換する。
>
> 7. **`ConfigWizard` は対象外**である(`app_bootstrap.py:16`、メインウィンドウ生成前)。
>    この判断をコード上のコメントとして残し、将来「ここもゲートを取るべきでは」という
>    誤った修正が入らないようにする。
>
> 8. **非同期スコープの解放経路表**を、この計画書の末尾に追記する。表#1-4, #6, #8, #11-13
>    それぞれについて、正常完了・失敗・タイムアウト・キャンセル・サーバー停止・アプリ終了の
>    6経路の解放地点をコード位置付きで列挙する。**Step 3 はこの表を入力とする。**
>
> 動作確認: `--debug` で、(a) 背景ロード中/構成ロード中/ハードウェア設定ダイアログ表示中に
> API から `/acquire` を叩くと409が返ること、(b) 各操作をキャンセルした場合・例外を
> 起こした場合にもゲートが解放されること(操作後に別の測定が開始できること)、
> (c) 背景取得の保存ダイアログを開いたままAPIを叩くと409になり、キャンセルすると解放される
> こと、(d) `POST /calibration` 実行中に `/acquire` が409になること。

---

## Step 1: UIロック破り経路の是正とロックヘルパーの冪等化

**優先度:** P0(Step 2 の前提条件)
**依存:** なし(Step 0 と並行可)
**対象ファイル:** `src/ui/ui_mixins/sequential_mixin.py`,
`src/ui/ui_mixins/spectrometer_control_mixin.py`, `src/ui/main_window.py`,
`src/ui/ui_mixins/acquisition_mixin.py`

**実行プロンプト:**

> 方針4に挙げた「ロック状態を無視してウィジェットを再有効化する経路」を塞ぎ、
> `_lock_ui`/`_unlock_ui` を冪等にする。**この2つは必ずセットで行うこと** — 冪等化だけを
> 先に入れると、誤って再有効化された状態が次のリクエストでも修復されなくなり、
> 現状より悪化する。
>
> 1. **`_reassert_ui_lock()` ヘルパーの新設**(`sequential_mixin.py`)。
>    ロック理由が1つでも立っていれば `set_ui_enabled_during_seq(False)` を呼び直す。
>    一括再有効化が避けられない箇所の直後に呼ぶための道具。
>
> 2. **経路1: `_close_spectrometer_moving_dialog()`**(`spectrometer_control_mixin.py:209-215`)。
>    `self.centralWidget().setEnabled(True)` が無条件であり、**API構成適用の完了時に必ず
>    通る**ため最も影響が大きい。対になる `_show_spectrometer_moving_dialog()` が
>    `setEnabled(False)` している(`:182`)ので対称性は保ちつつ、
>    ```python
>    self.centralWidget().setEnabled(True)
>    self._reassert_ui_lock()
>    ```
>    とする。`centralWidget()` を有効化してからロック状態を再適用する順序にすること
>    (先に再適用すると `setEnabled(True)` が上書きしてしまう)。
>    この関数は `:266`, `:315`, `:342` の3ヶ所から呼ばれるが、修正は関数側の1ヶ所で足りる。
>
> 3. **経路2: 露光時間適用完了**(`main_window.py:839`)。
>    `self.thread.exposure_set_finished.connect(lambda: self.spin_acq_time.setEnabled(True))`
>    のラムダを名前付きメソッド `on_exposure_set_finished()` に置き換え、
>    `self.spin_acq_time.setEnabled(not self._ui_lock_reasons)` とする。
>    **APIリクエストは毎回露光時間を設定しうる**(`_api_start_acquire` の
>    `thread.update_exposure()`)ため、連続実行ではこのシグナルが毎秒4回前後発火し、
>    そのたびにロック中の `spin_acq_time` が有効化されることになる。
>
> 4. **経路3: `apply_roi_settings()`**(`acquisition_mixin.py:423-437`)。
>    `spin_vstart` / `spin_vend` / `radio_bg_on` への `setEnabled` をロック考慮にする。
>    ただしこの関数は Step 4 で `_sync_roi_widget_states()` として切り出すため、
>    ここでは最小限の `and not self._ui_lock_reasons` の追加に留め、
>    構造変更は Step 4 に委ねる。
>
> 5. **`_lock_ui` の冪等化**(`sequential_mixin.py:9-16`)。連続実行では毎秒4回前後呼ばれるため、
>    既にロック中なら約35個のウィジェットを舐め直す処理をスキップする。
>    ```python
>    def _lock_ui(self, reason):
>        was_locked = bool(self._ui_lock_reasons)
>        self._ui_lock_reasons.add(reason)
>        if not was_locked:
>            self._capture_lock_focus()      # Step 4 で実装、ここでは no-op で置いておく
>            self.set_ui_enabled_during_seq(False)
>    ```
>
> 6. **`_unlock_ui` の冪等化**(`sequential_mixin.py:18-21`)。現状は存在しないreasonを
>    `discard` しても最後の1つが外れたかのように扱われうる。
>    ```python
>    def _unlock_ui(self, reason, reapply_hardware=True):   # 引数は Step 4 で使う
>        if reason not in self._ui_lock_reasons:
>            return
>        self._ui_lock_reasons.discard(reason)
>        if not self._ui_lock_reasons:
>            self.set_ui_enabled_during_seq(True)
>    ```
>
> 7. **網羅監査。** `grep -rn "setEnabled(" src/ui/` を実行し、ロック保持中に発火しうる
>    経路(カメラスレッドのシグナルハンドラ、`temp_poll_timer`、移動完了コールバック、
>    `on_em_gain_set_finished`、`on_temperature_set_finished`、`on_roi_applied`)を
>    すべて洗い出す。既にガードのあるもの(`acquisition_mixin.py:128`, `:142`, `:155`、
>    `spectrometer_control_mixin.py:163-165`)を除き、ガードの無いものを列挙して作業ログに
>    記載し、必要なものを修正する。
>    **`on_camera_initialized` / `on_camera_init_failed`(`acquisition_mixin.py:295`, `:404`)は
>    修正対象外**とする — 初期化完了時のみ発火し、Standbyの自動待ち受け開始はその後に行う
>    ため(方針2の除外理由)。この判断をコメントで残すこと。
>
> 8. `tests/test_ui_lock_reasons.py` を新規作成し、Qt offscreen で以下を検証する:
>    `_lock_ui` の二重呼び出しがウィジェット走査を1回しか行わないこと、
>    存在しないreasonの `_unlock_ui` が何もしないこと、
>    `_close_spectrometer_moving_dialog()` の後もロックが維持されること(経路1の回帰テスト)、
>    `exposure_set_finished` 相当のシグナル後も `spin_acq_time` が無効のままであること
>    (経路2の回帰テスト)。
>
> 動作確認: `--debug` で "Start API Server"(現行の locked 相当)を押した状態で、
> (a) `POST /configurations/{id}/apply` を叩き、移動完了後も測定系コントロールが
> 無効のままであること、(b) `POST /acquire` を露光時間指定付きで叩き、完了後も
> `spin_acq_time` が無効のままであること、(c) Stop 後にすべて正常に復帰すること。

---

## Step 2: ゲート所有者の導入とUIロックの導出

**優先度:** P0
**依存:** Step 0, Step 1
**対象ファイル:** `src/ui/ui_mixins/acquisition_mixin.py`, `src/ui/ui_mixins/api_mixin.py`,
`src/ui/main_window.py`

**実行プロンプト:**

> `_acquisition_gate` の所有者を記録し、そこからUIロックを導出する。**このStepでは外形的な
> 挙動を変えない**(APIサーバーは従来通り起動中ずっとロックする)。
>
> 1. `main_window.py` の `__init__` に `self._gate_owner = None` を追加する
>    (既存の `self._gate_held_by_me = False` の隣、`main_window.py:125` 付近)。
>
> 2. `_try_acquire_gate` に `owner` 引数を追加する(既定 `"gui"`)。
>    ```python
>    def _try_acquire_gate(self, owner="gui") -> bool:
>        """測定権の排他ゲートを非ブロッキングで取得する。既に誰かが握っていれば False を返す。
>
>        owner は "gui"(操作者の手動操作)か "api"(APIリクエスト由来)。owner=="api" の間だけ
>        UIロック理由 "api_active" が立つ。GUIスレッドからのみ呼ぶこと(方針4の不変条件)。
>        """
>        if self._acquisition_gate.acquire(blocking=False):
>            self._gate_held_by_me = True
>            self._gate_owner = owner
>            if owner == "api":
>                self._lock_ui("api_active")
>            return True
>        return False
>    ```
>
> 3. `_release_acquisition_gate` を、所有者を見てロックを解除するようにする。
>    **ゲート解放を `_unlock_ui` より先に行うこと。** 順序を逆にすると、UI再有効化の
>    シグナル処理中に操作者がボタンを押せてしまい、まだ解放されていないゲートに対して
>    409相当のメッセージが出る。
>    ```python
>    def _release_acquisition_gate(self) -> None:
>        if getattr(self, '_gate_held_by_me', False):
>            self._gate_held_by_me = False
>            owner = self._gate_owner
>            self._gate_owner = None
>            self._acquisition_gate.release()
>            if owner == "api":
>                self._unlock_ui("api_active")   # Step 4 でデバウンス化する
>    ```
>
> 4. Step 0 で追加した分を含め、API側の全ゲート取得を `owner="api"` にする:
>    `_api_begin_hardware_refresh`(`:133`)、`_api_start_acquire`(`:259`)、
>    `_api_start_configuration_apply`(`:357`)、`api_apply_calibration`(Step 0 で追加)。
>    GUI側(`file_io_mixin.py` の4ヶ所 + Step 0 の追加分、
>    `spectrometer_control_mixin.py` の2ヶ所)は既定値 `"gui"` のままとし**変更しない**。
>
> 5. `_api_start_acquire` の `gate_already_held=True` 経路は、既に owner=="api" で
>    ゲートを握っているため追加のロックは不要。既存の
>    `if gate_already_held and not self._acquisition_gate.locked()` チェックはそのまま残す。
>
> 動作確認: `--debug` で (a) "Start API Server" で従来通りロックされること、
> (b) `POST /acquire` の前後でロック状態が変わらないこと(サーバー起動中は `api_server` 理由が
> 常に立っているため)、(c) Stop で復帰すること。サーバーを起動せずにローカルの単発/連続/
> Sequential測定を行い、ゲートの取得/解放が従来通り動作することを確認する。
> 「各ステップ共通の検証手順」の5(ゲート解放の回帰)を必ず実施すること。

---

## Step 3: 取得の異常系 — カメラ未起動・タイムアウト・遅延フレーム・サーバー停止

**優先度:** P0
**依存:** Step 2、Step 0 の解放経路表、Step 7a(スナップショットにキャッシュが要るため)
**対象ファイル:** `src/ui/ui_mixins/api_mixin.py`, `src/api/server.py`,
`src/api/schemas.py`, `src/ui/ui_mixins/acquisition_mixin.py`

**実行プロンプト:**

> Standbyでは「初期化に失敗した状態のまま待ち受けている」「クライアントが落ちた」といった
> 異常系が日常的に起きうる。**これらでゲートが解放されないと、GUIが永久にロックされたまま
> になる**(Step 2 でロックがゲートに従属したため)。以下を実装する。
>
> **(A) カメラready検証 — 503**
>
> 1. `_api_start_acquire()` に**カメラ稼働確認がない**(`api_mixin.py:250-305`)。
>    関数の先頭、**ゲート取得より前**に `self.thread.isRunning()` を確認し、未起動なら
>    新設の `CameraNotReadyError` を送出する。ゲート取得前に確認するのは、解放漏れの
>    経路をこれ以上増やさないため。
> 2. `server.py` で `CameraNotReadyError` を **503** に変換する(機器の一時的な不在であり、
>    クライアントのリクエスト不備ではないため4xxではない)。detail に「カメラが初期化されて
>    いない」旨を含める。`/acquire`, `/acquire/fit`, `/acquire/pressure` の3経路すべてに効く
>    よう `_run_acquire()` に追加する。
>
> **(B) 取得タイムアウトの清掃**
>
> 3. `api_acquire()` の `result = future.result(timeout=acquisition_timeout)`
>    (`api_mixin.py:583`)には `finally` がなく、タイムアウト時にゲートと
>    `_api_pending_future` が残る。`FutureTimeoutError` を捕捉し、GUIスレッドで清掃する。
>    ```python
>    def _api_abort_acquire(self, future):
>        """GUIスレッドで実行。タイムアウトしたAPI取得の後始末。"""
>        if getattr(self, "_api_pending_future", None) is future:
>            self._api_pending_future = None
>        self._active_target_accum = None
>        self.is_single_shot = False
>        if getattr(self.thread, "is_measuring", False):
>            # stop_measurement() が内部で _release_acquisition_gate() を呼ぶ。
>            # _gate_held_by_me ガードにより二重解放にはならない。
>            self.stop_measurement()
>        else:
>            self._release_acquisition_gate()
>    ```
>    **カメラを止めてからゲートを解放する順序が重要**である。まだ測定中のままゲートだけ
>    解放すると、次の取得が走り出してハードウェアが競合する。
> 4. ワーカースレッド側は
>    `finally: self.gui_bridge.call(lambda: self._api_abort_acquire(future))` ではなく、
>    **タイムアウト時のみ**呼ぶこと(正常完了時は既存の解放経路が動くため、無条件に
>    呼ぶと二重処理になる)。504 を返して終える。
>
> **(C) 遅延フレームの扱い**
>
> 5. `future.result(timeout=...)` のタイムアウトはFutureをキャンセルしないため、Futureは
>    pending のまま残る。上記3で `_api_pending_future` を None にするので、遅れて到着した
>    フレームは `_process_completed_data()` の通常のGUI表示パスへ流れる。
>    **これは許容する**(プロットが更新されるだけ)。ただし `_process_completed_data()` が
>    `self._api_pending_future` を参照する箇所で `None` を正しく扱うこと、および
>    `_active_target_accum` が既にリセット済みであることを確認する。
>    この設計判断をコメントとして残す。
>
> **(D) サーバー停止セマンティクス**
>
> 6. 方針6に従い、**Offへの切替は「新規受付の停止」であり in-flight の中断ではない**。
>    現行の `stop_api_server()`(`api_mixin.py:801-807`)は `should_exit=True` と5秒 join
>    だけで、取得が5秒を超えるとスレッドが生きたまま参照が消える。次のように変更する:
>    - 停止要求時にまず `self._api_accepting = False` を立て、`verify_api_key` より前段の
>      依存性で **503 "server is shutting down"** を返す(新規受付の即時停止)。
>    - `should_exit = True` を設定する。
>    - **GUIスレッドをブロックしない。** `QTimer`(200ms周期)でスレッドの `is_alive()` を
>      監視し、終了したら参照をクリアしてモードUIを更新する。それまでパネルは
>      "Stopping…" を表示し、モード選択は無効にする。
>    - **スレッドが終了するまで `self._api_server` / `self._api_server_thread` の参照を
>      消さない。** これが現行の主要な欠陥である。
> 7. `closeEvent`(`main_window.py:871-872`)はアプリ終了なので有限時間で諦める必要がある。
>    サーバースレッドは daemon なのでプロセス終了とともに落ちる。ただし in-flight の
>    ハードウェア操作が中断されうるため、`api_active` ロックが立っている状態で閉じようと
>    した場合は確認ダイアログを出す。`_api_unlock_timer` の停止も追加する。
>
> 8. `tests/test_api_acquire_failures.py` を新規作成し、以下を検証する:
>    カメラ未起動で `/acquire` が503になりゲートが取られないこと、
>    取得タイムアウト後に `_acquisition_gate.locked()` が False に戻ること、
>    タイムアウト後に遅延フレームが届いても例外が出ないこと、
>    停止処理中の新規リクエストが503になること。
>
> 動作確認: `--debug` で (a) カメラスレッドを意図的に停止させた状態で `/acquire` が503を
> 返し、その後もGUIが操作可能なこと、(b) 極端に短い timeout を指定して504を発生させ、
> その後にGUIから測定を開始できること(ゲートが解放されている)、(c) 長い取得の最中に
> モードをOffにすると "Stopping…" が表示され、取得完了後に停止が完了すること、
> (d) その間の新規リクエストが503になること。
> `TODO(実機確認待ち)`: 実機の長時間積算中のタイムアウト清掃で、カメラが正しく停止するか。

---

## Step 4: 解除経路の分離とデバウンス

**優先度:** P0
**依存:** Step 2
**対象ファイル:** `src/ui/ui_mixins/sequential_mixin.py`, `src/ui/ui_mixins/acquisition_mixin.py`,
`src/ui/ui_mixins/api_mixin.py`, `src/ui/main_window.py`, `src/ui/local_cache.py`

**実行プロンプト:**

> **(A) 解除経路の分離**
>
> 1. `set_ui_enabled_during_seq(enabled)` は末尾で `apply_roi_settings()` を呼び
>    (`sequential_mixin.py:103-105`)、その中で `self.thread.update_roi_settings(mode, ...)` という
>    **カメラスレッドへの実際の設定送出**が行われる(`acquisition_mixin.py:439`)。
>    リクエスト単位で解除するようになるとリクエストのたびにROIを押し込むことになる。
>
> 2. `apply_roi_settings()` から、ウィジェットの enabled 状態と背景ラジオの整合を取る部分を
>    `_sync_roi_widget_states()` として抽出する(Step 1 の手順4で入れたロック考慮も
>    こちらへ移す)。`apply_roi_settings()` は `_sync_roi_widget_states()` を呼んでから
>    `self.thread.update_roi_settings(...)` を呼ぶ形にする(外部呼び出し側の挙動は不変)。
>
> 3. `set_ui_enabled_during_seq(self, enabled, reapply_hardware=True)` に引数を追加する。
>    `True` のときは従来通り `apply_roi_settings()` を、`False` のときは
>    `_sync_roi_widget_states()` のみを呼ぶ。`toggle_fitting_panel()` は純粋なUI操作なので
>    どちらの場合も呼ぶ。
>
> 4. Step 1 で用意した `_unlock_ui(reason, reapply_hardware=True)` の引数を実際に伝播させる。
>    Sequential終了時と `locked` モードのサーバー停止時は `True`(現行挙動を厳密に保存)、
>    API由来の `api_active` 解除は `False`。
>
> **(B) デバウンス**
>
> 5. `main_window.py` の `__init__` にシングルショットの `QTimer` を用意する。
>    ```python
>    self._api_unlock_timer = QTimer(self)
>    self._api_unlock_timer.setSingleShot(True)
>    self._api_unlock_timer.timeout.connect(self._on_api_unlock_timeout)
>    self._api_unlock_delay_ms = int(cache.get("api_unlock_delay_ms", 1000))
>    self._api_lock_focus_widget = None
>    ```
>    既定1000msの根拠(方針5): closed-loop では次リクエストまでの間隔が「クライアントの
>    応答処理 + ネットワーク往復」だけであり取得時間に依存しないため、実測の連続測定間隔
>    (概ね250ms)より確実に短い。1秒はこれに対して十分な余裕がある。連続実行中は
>    タイマーが一度も満了せずロックが継続する。
>
> 6. Step 2 で `_release_acquisition_gate` に入れた `self._unlock_ui("api_active")` を
>    `self._api_unlock_timer.start(self._api_unlock_delay_ms)` に置き換える
>    (`QTimer.start()` は実行中のタイマーを再スタートするので明示的な停止は不要)。
>    `_try_acquire_gate(owner="api")` 側では `self._api_unlock_timer.stop()` を呼ぶ。
>
> 7. `_on_api_unlock_timeout()` を実装する。**発火時点で再確認すること:**
>    ```python
>    def _on_api_unlock_timeout(self):
>        # タイマー発火とゲート再取得が競合しうるため、発火時点で改めて確認する。
>        if self._gate_owner == "api":
>            return
>        self._unlock_ui("api_active", reapply_hardware=False)
>        self._restore_api_lock_focus()
>    ```
>
> **(C) フォーカス復帰**
>
> 8. Step 1 で no-op として置いた `_capture_lock_focus()` を実装する
>    (`QApplication.focusWidget()` を `self._api_lock_focus_widget` に記録)。
>    `_restore_api_lock_focus()` は、そのウィジェットが (i) 生存 (ii) `isVisible()`
>    (iii) `isEnabled()` を満たすときのみ `setFocus()` する。PyQt6ではC++側が破棄された
>    ウィジェットへのアクセスが `RuntimeError` を送出するため `try/except RuntimeError` で
>    囲む。復帰後は `None` に戻す。
>
> 9. デバウンス秒数を `local_cache.py` 経由で永続化可能にする(キー `api_unlock_delay_ms`、
>    既定1000)。GUIからの変更UIは Step 5 で追加する。
>
> 動作確認: `scripts/_dev_api_load.py` を用意し、「各ステップ共通の検証手順」の連続実行
> スクリプトに挙げた全項目(closed-loop、全リクエスト200)を確認する。加えて、
> (a) 長時間の積算取得(露光1秒×10積算等)をAPI経由で1回だけ実行し、取得中ずっとロックが
> 維持され完了の約1秒後に解除されること(=タイマー起点がゲート解放時であること)、
> (b) Sequential測定の開始/終了で `reapply_hardware=True` 経路が従来通り動作すること。

---

## Step 5: Standbyモード本体

**優先度:** P0
**依存:** Step 2, Step 3, Step 4
**対象ファイル:** `src/ui/ui_mixins/api_mixin.py`, `src/ui/main_window.py`,
`src/ui/local_cache.py`, `src/api/server.py`

**実行プロンプト:**

> 方針1の3状態モデルを実装する。
>
> 1. モードは `"off"` / `"standby"` / `"locked"` の文字列。`standby` と `locked` はどちらも
>    uvicorn サーバーを起動する。**唯一の違いは `locked` が起動時に `_lock_ui("api_server")`
>    を掛け停止時に外すこと**。`standby` は `api_server` 理由を使わず Step 2・4 の
>    `api_active` だけに任せる。既存の `start_api_server`/`stop_api_server` はサーバーの
>    起動停止のみを担うよう整理し、UIロックはモードに応じて呼び出し側で掛ける。
>
> 2. `local_cache.py` に追加するキー: `api_mode`(既定 `"off"`)、`api_port`(既定8765)、
>    `api_bind_host`(既定 `"0.0.0.0"`)、`api_unlock_delay_ms`(既定1000)、
>    `api_expose_docs`(既定 `False`)。**`spectrometerConfig.json` には入れない** —
>    あちらは機器構成(grating一覧・既定ROI・flip_x・model)を表すファイルであり、
>    APIの運用設定はマシン固有の運用状態なので local cache が適切。
>
> 3. **起動時の自動開始。** 永続化されたモードが `off` でない場合、ハードウェア初期化の
>    完了後に自動的にサーバーを起動する。`on_camera_initialized` と初期化失敗パス
>    (`on_camera_init_failed`)の**両方**から呼ぶこと。**この順序は方針2の除外判断の前提で
>    あり、崩してはならない** — 初期化ハンドラは `centralWidget().setEnabled(True)` を
>    無条件で行うため、それより前に待ち受けを開始するとロックが破られる。
>    二重起動を防ぐため `getattr(self, '_api_server', None) is not None` を先頭でチェックする。
>    初期化に失敗した状態で `/acquire` が来た場合は Step 3(A) の 503 に落ちる。
>
> 4. **サーバー起動失敗の処理。** ポートが使用中の場合、uvicorn はバックグラウンド
>    スレッド内で失敗する。現在の実装は起動の成否を確認していない。起動後に短いポーリング
>    (`server.started` 属性、または `QTimer` で数百ms後に確認)で待ち受け開始を検証し、
>    失敗していればモードを `off` に戻して `QMessageBox` で通知する。**自動開始時に
>    黙って失敗すると、操作者はAPIが動いていると誤解したまま実験を始めることになる。**
>
> 5. **APIパネルの刷新**(`main_window.py:602-627`)。現行の Start/Stop 2ボタンを置き換える:
>    - モード選択(`QComboBox` または3つのラジオ: Off / Standby / Locked)
>    - ポート番号、bind host(`0.0.0.0` / `127.0.0.1` / 検出したローカルIP)
>    - 解除待ち時間(0.5-10.0秒、既定1.0)
>    - "Expose /docs" チェックボックス(既定オフ)
>    - 状態ラベル: URL、現在のモード、`Last request: <client名> (<IP>) <時刻>`、
>      停止処理中は "Stopping…"
>    - **"Remote control active" インジケータ** — `api_active` ロックが立っている間だけ
>      点灯する。Standbyでは操作者が「今リモートが動かしている」ことを一目で判別できる
>      必要があるため必須要素とする。
>
>    APIパネル自体は従来通り `set_ui_enabled_during_seq` の対象に**含めない**
>    (ロック中でもモードを `off` に戻せる必要があるため)。ただし方針6の通り、
>    Off切替は in-flight を中断しない。**モード切替時に `api_active` が立っている場合は、
>    「進行中のリモート操作は完了まで継続します」と明示する確認ダイアログを出す**
>    (初版の「in-flightは失敗する」という説明は誤りなので、UI文言に持ち込まないこと)。
>
> 6. **409 detail の具体化**(`server.py:106-107`, `:126`, `:271`)。現状 `"acquisition busy"` /
>    `"instrument busy"` という文字列しか返さないため、Standbyでは409の頻度が上がったときに
>    リモート側から原因が分からない。Step 0 の `_gate_busy_reason()` を実装し、
>    `_gate_owner` と `is_sequential_running` から
>    `{"code": "busy", "reason": "local_sequential_run" | "local_operator_action" |
>    "another_api_request", "message": ...}` を返す。
>
> 動作確認: `--debug` で、
> (a) Standbyを選ぶとサーバーが起動するがUIは操作可能なままであること、
> (b) `POST /acquire` でその瞬間だけロックされ "Remote control active" が点灯し約1秒後に
>     復帰すること、
> (c) 再起動するとStandbyのまま自動的に待ち受けを再開すること、
> (d) Lockedが**現行と完全に同一の挙動**であること、
> (e) Sequential測定中の `/acquire` が409を返し reason が `local_sequential_run` であること、
> (f) API操作中にハードウェア設定ダイアログを開こうとすると拒否されること(Step 0 の効果)、
> (g) 使用中のポートを指定してStandbyにすると、モードが `off` に戻り通知が出ること、
> (h) 取得の最中にモードをOffに切り替えると確認ダイアログが出て、承認後 "Stopping…" を経て
>     取得完了後に停止すること。

---

## Step 6: 名前付き鍵とクライアント認可

**優先度:** P0
**依存:** なし(Step 0-5 と並行可)
**対象ファイル:** `src/api/server.py`, `src/ui/ui_mixins/config_mixin.py`,
`src/ui/ui_mixins/api_mixin.py`, `src/ui/main_window.py`
**新規:** `src/ui/menu/api_clients_dialog.py`, `tests/test_api_clients.py`

**実行プロンプト:**

> 方針7に従い、単一鍵を名前付き鍵のリストに置き換える。
>
> 1. **保存場所の移動。** 現在の `fluora_pressee_api_key.json` はリポジトリ直下の平文JSONで
>    ある(`config_mixin.py:168`)。`ConfigurationCatalog` が使っている
>    `default_configuration_root()`(`configuration_catalog.py:60-68`)と同じ流儀で、
>    アプリケーションデータディレクトリ配下(`FluoraPressee/api_clients.json`)へ移す。
>    旧パスにファイルがあれば読み込んで新パスへ移行し、旧ファイルは削除する。
>
> 2. **ファイル形式 v2。**
>    ```json
>    {
>      "version": 2,
>      "clients": [
>        {"name": "press-controller", "key": "...", "allowed_ips": ["192.168.1.42"],
>         "created": "2026-08-07T12:00:00"}
>      ]
>    }
>    ```
>    **移行:** 旧形式(`{"api_key": "..."}`)を読んだ場合、その鍵を `name: "default"`,
>    `allowed_ips: []`(=制限なし)の1クライアントとして取り込む。**既存クライアントは
>    設定を一切変更せずに動き続けること**が必須要件。`allowed_ips` が空リストなら
>    任意のIPを許可する(移行互換)。
>
> 3. **「初回」と「明示的な空」の区別**(初版の矛盾点)。
>    - **ファイルが存在しない**(初回起動) → 鍵を1件自動生成して保存する。
>    - **ファイルが存在し `clients: []`** → 操作者が全件削除した意思表示として尊重し、
>      自動生成しない。この状態では全リクエストが401になる。
>    管理ダイアログで最後の1件を削除する際は「APIが利用不能になる」旨を警告する。
>
> 4. **原子的保存。** 一時ファイルへ書いてから `os.replace()` で差し替える。POSIXでは
>    書き込み前に `os.chmod(path, 0o600)` で所有者限定にする(Windowsでは chmod がほぼ
>    無効である点をコメントに明記し、保存場所がユーザープロファイル配下であることを
>    もって代替とする)。
>
> 5. **スレッド安全性。** クライアント一覧はGUIスレッドで編集され、認証時は複数の
>    ワーカースレッドから読まれる。**最終アクセスdictだけでなく一覧そのものが対象である。**
>    一覧は **immutable なスナップショット(`tuple`)として保持し、編集時は新しい tuple を
>    作って参照ごと差し替える**。読み手は参照を1回読むだけなので追加のロックは不要
>    (参照の代入はアトミック)。最終アクセス記録は別途 `threading.Lock` で保護した dict と
>    する。**この処理で `gui_bridge.call()` を使ってはならない** — 連続実行ではリクエストごとに
>    ワーカースレッドがGUIスレッドの応答を待つことになり性能を著しく損なう。
>
> 6. `config_mixin.py:166-176` の `load_api_key_file()`/`save_api_key_file()` を
>    `load_api_clients()`/`save_api_clients()` に置き換える。移行ロジックは前者に置く。
>
> 7. **`verify_api_key` の刷新**(`server.py:70-79`)。
>    ```python
>    def verify_api_key(request: Request, x_api_key: str | None = Header(default=None)):
>        if x_api_key is None:
>            raise HTTPException(status_code=401, detail="Missing X-API-Key header")
>        ...
>    ```
>    - 鍵の比較は `secrets.compare_digest` を使う(定数時間比較)。
>    - 一致した鍵の `allowed_ips` と要求元IPを照合する。**CIDR記法に対応する**
>      (`ipaddress.ip_network(entry, strict=False)` / `ipaddress.ip_address(host)`)。
>    - **IPv4射影アドレスの正規化を忘れないこと。** デュアルスタック待ち受けでは
>      `request.client.host` が `::ffff:192.168.1.42` の形になりうる。
>      `ipaddress.ip_address(host).ipv4_mapped` があればそちらを使う。
>    - `request.client` が `None` になる場合(テストクライアント等)の扱いを決めること。
>      `allowed_ips` が空なら許可、非空なら拒否とする。
>    - 鍵が未知 → **401**。鍵は有効だがIPが許可外 → **403**(detail にクライアント名と
>      要求元IPを含める)。403は「その鍵は有効である」ことを漏らすが、研究室運用では設定ミスの
>      切り分けが圧倒的に重要なためこの情報開示を受容する。**この判断をコメントに残すこと。**
>    - 一致したクライアント名を `request.state.api_client` に格納する。
>
> 8. **`/docs` の閉塞。** `create_app` に `expose_docs: bool = False` を追加し、False のとき
>    `FastAPI(title=..., docs_url=None, redoc_url=None, openapi_url=None)` とする。
>    現行は `verify_api_key` が router の依存性(`server.py:81`)であるため自動ドキュメントが
>    無認証で露出している。Step 5 のチェックボックスから制御する。
>
> 9. **クライアント管理ダイアログ**を `src/ui/menu/api_clients_dialog.py` に新規作成する
>    (メニュー系ダイアログは `src/ui/menu/` に置くというディレクトリ規約に従う)。
>    テーブル(名前 / 鍵のマスク表示+reveal+copy / 許可IP / 作成日時 / 最終アクセス)と、
>    追加・改名・許可IP編集・そのクライアントのみの鍵再発行・失効。
>    `main_window.py:719-721` のメニュー "API → Regenerate Key" を "API → Manage Clients" に
>    置き換え、`on_regenerate_api_key_clicked` と `regenerate_api_key()` を削除する。
>
> 10. `_build_api_status_text()`(`api_mixin.py:832-837`)が `X-API-Key: {self._api_key}` を
>     パネルに直接表示しているが、複数クライアント化に伴いこれをやめ「鍵は Manage Clients で
>     確認」という案内に変更する。`self._api_key`(`main_window.py:90`)への参照を全て除去する。
>
> 11. `tests/test_api_clients.py` を新規作成し、Qt非依存で検証する:
>     v1→v2移行と旧パスからの移行、`compare_digest` による照合、`allowed_ips` 空=全許可、
>     CIDR照合、IPv4射影アドレスの正規化、未知の鍵で401、許可外IPで403、
>     ファイル不存在で自動生成 / `clients: []` で自動生成しない、原子的保存
>     (書き込み中にプロセスが落ちても既存ファイルが壊れない)。
>
> 動作確認: `--debug` で (a) 旧パスの v1 鍵ファイルがある状態で起動すると新パスの v2 へ
> 移行され、**移行前の鍵がそのまま使えること**、(b) 2件目を追加し片方に自分のIP・もう片方に
> 別のIPを設定して前者は200・後者は403になること、(c) 1件失効させても他方が影響を受けない
> こと、(d) "Expose /docs" オフで `/docs` と `/openapi.json` が404になること。
> `TODO(実機確認待ち)`: 別マシンのクライアントからLAN越しにIPアロウリストが期待通り
> 機能するかは実機LANで確認する。

---

## Step 7: 構成レコードキャッシュ(7a)と instrument state token(7b)

**優先度:** 7a=P0(Step 3 の前提), 7b=P1
**依存:** 7a=なし / 7b=Step 5
**対象ファイル:** `src/core/configuration_catalog.py`, `src/ui/ui_mixins/api_mixin.py`,
`src/api/schemas.py`, `src/api/server.py`, `src/ui/ui_mixins/file_io_mixin.py`,
`src/ui/ui_mixins/spectrometer_control_mixin.py`, `src/ui/ui_mixins/acquisition_mixin.py`,
`src/ui/main_window.py`

### Step 7a: 構成レコードのメモリキャッシュ

**実行プロンプト:**

> 1. `ConfigurationCatalog.get_configuration()`(`configuration_catalog.py:812-831`)は
>    SQLiteクエリ → ファイル読み込み → SHA-256検証を毎回行う。方針9のスナップショットは
>    これを**ゲート保持中のGUIスレッド**で行うため、キャッシュが前提条件となる。
>
> 2. `configuration_id` をキーとしたメモリキャッシュを追加する。レコードは immutable なので
>    内容は変わらない。無効化はこのクラスの書き込み系メソッド(保存・アクティブ化)から
>    一括クリアするだけでよい。
>
> 3. **キャッシュは `threading.Lock` で保護すること。** APIワーカースレッドからも
>    `api_list_configurations` 等の経路で読まれる。
>
> 4. **返す値は `copy.deepcopy()` する。** `dict` をそのまま返すと呼び出し側の破壊的変更が
>    キャッシュを汚染する。
>
> 5. **SHA-256検証が初回読込時のみになる**トレードオフを、クラスの docstring に明記する。
>    これは「このプロセスが catalog の唯一の書き手である」という既存の前提と整合する。
>    別プロセスが同じ catalog に書いた場合キャッシュは古いままになる点も併記する。

### Step 7b: instrument state token

**実行プロンプト:**

> 方針8・方針9を実装する。**クライアントに新しい必須項目を課さないこと**が要件。
>
> 6. `main_window.py` の `__init__` で `self._instrument_state_epoch = uuid.uuid4().hex[:8]`,
>    `self._instrument_state_counter = 0` を初期化し、
>    `instrument_state_token` プロパティが `f"{epoch}:{counter}"` を返すようにする。
>    **クライアントは中身を解釈せず等値比較のみ行う不透明な値**である旨をコメントと
>    ドキュメントの両方に明記する。
>
> 7. `_bump_instrument_state()` を、**取得結果を変えうるすべての状態変更**から呼ぶ。
>    初版の一覧は不足していた。正しい一覧:
>    - 分光器のApply完了(グレーティング / 中心波長)— `spectrometer_control_mixin.py`
>    - 校正の適用・無効化 — `apply_calibration()` / `deactivate_axis_calibration()`
>    - ROIの変更(`apply_roi_settings()` のハードウェア送出側)
>    - 背景のロード・取得・クリア — `file_io_mixin.py`
>    - Wavelength/Raman表示切替、励起波長の変更
>    - **露光時間**(`spin_acq_time`)
>    - **積算数**(`spin_accumulate`)
>    - **EM gain**(`spin_em_gain`)
>    - **cosmic-ray removal の有無と閾値**(`chk_cosmic_ray_removal` / `spin_spike_threshold`)
>    - **検出器温度設定値**(`spin_cooler_temp`)
>
>    重複してインクリメントされても問題ない(単調増加であればよく増分に意味は無い)。
>    ただしAPI自身が設定する露光時間・積算数(`_api_start_acquire`)でインクリメントすると
>    自分のリクエストで自分のtokenを無効化することになる。**API由来の変更は
>    インクリメントしない**か、比較の後に行うかを決めて明記すること
>    (比較地点が「ゲート取得直後・状態変更前」なので、後者なら問題は起きない)。
>
> 8. **比較地点の集約。** `_assert_instrument_state_token(expected)` を作り、
>    **ゲートを取得した直後・状態を変える前**に呼ぶ。呼び出し元は2ヶ所:
>    - `configuration_id` あり → `_api_start_configuration_apply()` 内
>    - `configuration_id` なし → `_api_start_acquire()` 内
>
>    これで初版の欠陥(構成適用自身がtokenを変えるため常に不一致になる)が解消され、
>    両者の併用が正しく成立する。不一致なら `StateTokenMismatchError` を送出し、
>    `server.py` で **409** に変換する。detail に現在のtokenを含め、クライアントが
>    再同期できるようにする。
>
> 9. **応答へのtoken追加。** `_api_configuration_state()` の戻り値に加えるだけでは不十分。
>    - `api_mixin.py:478-480` のコメントの通り、この dict は `**state` で展開され pydantic の
>      `extra="ignore"` により**宣言していないスキーマでは黙って捨てられる**。
>      `StatusResponse` / `AcquireResponse` / `ApplyConfigurationResponse` の
>      3つすべてにフィールドを宣言する。
>    - **さらに `_acquire_response_payload()`(`server.py:134-150`)は返却フィールドを
>      明示列挙している。** ここにも `instrument_state_token` を追加しないと acquire 応答には
>      出ない。
>
> 10. **応答stateの原子性**(方針9)。`_process_completed_data()` の中で、**ゲートを解放する
>     前に**応答に必要な state(`_api_configuration_state()` の結果、x軸、温度、背景データと
>     ミスマッチ判定)をまとめてスナップショットし、Futureのペイロードに載せる。
>     `_api_pending_future` が None でないときだけスナップショットを作ること(GUI取得でも
>     この関数は通るため)。`api_acquire()` は `_api_finalize_acquire()` と
>     `_api_configuration_state()` の追加 `gui_bridge.call()` をやめ、ペイロードから読む。
>     **これは正しさの修正であると同時に、`/acquire` あたりの往復を3回から1回へ減らす。**
>
> 11. `AcquireRequest`(`schemas.py:47`)に `expected_state_token: str | None = None` を
>     追加する。**必須にしない。**
>
> 12. `tests/test_instrument_state_token.py` を新規作成し、検証する:
>     状態変更でtokenが変わること、epochが起動ごとに変わること、
>     `configuration_id` と `expected_state_token` の併用が成立すること
>     (構成適用前の値を渡して成功すること)、不一致で409になり detail に現在値が含まれること、
>     省略時は従来通り成功すること、応答ペイロードにtokenが含まれること。
>
> 動作確認: `--debug` で (a) `GET /status` にtokenが含まれること、(b) GUIでROI・中心波長・
> **露光時間・積算数**を変更するとtokenが変わること、(c) 古いtokenを指定した `/acquire` が
> 409になること、(d) `configuration_id` と `expected_state_token` を同時指定して成功すること、
> (e) 省略時は従来通り成功すること、(f) 連続実行スクリプトでキャッシュとスナップショット
> 導入前後の達成レートとGUIスレッド応答性を比較すること(方針11の相対評価)。

---

## Step 8: ドキュメント整備

**優先度:** P1
**依存:** Step 5, Step 6, Step 7
**対象ファイル:** `docs-site/docs/api/`, `README.md`, `README_ja.md`, `CLAUDE.md`

**実行プロンプト:**

> 1. `docs-site/docs/api/` に Standbyモードの説明を追加する。3状態の表、Standbyでは
>    「API操作中のみGUIがロックされる」こと、解除待ち時間の意味、**Offへの切替が in-flight を
>    中断しない**ことを記載する。
>    新規ページは既存ページ(`acquire.md`, `configurations.md` 等)の frontmatter と
>    `sidebar_position` の付け方に倣うこと。Docusaurus は `onBrokenLinks: 'throw'` 設定なので、
>    相互リンクと画像パスは必ずローカルビルドで検証する。
>    (初版で参照していた `add-docs-page` スキルは `~/.claude/skills/` のユーザーレベル
>    スキルでありリポジトリには存在しないため、この計画書からの参照は外した。)
> 2. 名前付き鍵のページを追加する。クライアントの追加手順、IPアロウリストの書き方
>    (単一IP / CIDR)、失効手順、v1からの自動移行、鍵ファイルの新しい保存場所について記載する。
> 3. **セキュリティ上の前提を明記する。** 通信はTLSで保護されておらず、鍵は同一
>    ネットワークセグメント上で受動的に傍受されうる。Standbyは信頼できるLAN内での運用を
>    前提とする(方針7で意図的にTLSを見送った判断の記録)。
> 4. `instrument_state_token` と `expected_state_token` をAPIリファレンスに追加する。
>    **不透明な値として扱い、中身を解釈しないこと**を明記する。
>    新設したHTTPステータス(503 = カメラ未初期化 / 停止処理中、403 = IP許可外、
>    409 = token不一致)を `errors.md` に追加する。
> 5. `CLAUDE.md` のAPI節を更新する。特に「It only starts when the user explicitly clicks
>    "Start API Server"」および「While the API server is running, the GUI's measurement/config
>    controls are disabled」という記述は本計画で**事実でなくなる**ため書き換える。
> 6. `README.md` / `README_ja.md` のAPI記述を対で更新する。

---

## 実装しない / 見送る事項

- **TLS(HTTPS)対応。** 自己署名証明書の配布とクライアント設定のコストに対し、LAN内運用での
  見返りが薄い。方針7の通り、鍵が傍受可能なままである点を受容してドキュメントに明記する
  形で対応する。信頼できないネットワークでの運用が必要になった時点で再検討。
- **in-flight リクエストの強制キャンセル。** 方針6の通り、取得中のカメラ停止や移動中の
  分光器停止は実機での安全性検証が必要であり本計画のスコープ外。Offは新規受付の停止のみ。
- **`configuration_id` / `expected_state_token` の必須化。** 運用上の取り扱いが煩雑になる
  ため、どちらも任意のまま残す。
- **リモートからのdark再取得**(`POST /background/acquire` 相当)。`work_API.md` の判断通り、
  シャッター制御が未実装のため引き続きスコープ外。
- **リクエストのレート制限。** 連続測定相当の頻度を前提とするため閾値の設定が難しく、誤って正規の
  クライアントを弾くリスクの方が大きい。ゲートによる排他で十分と判断する。
- **鍵ファイルの暗号化。** OSのファイル権限以上の保護はパスフレーズ入力なしには実現できず、
  常時起動という要件と両立しない。保存場所の移動と権限制限までとする。

## オープンな課題

1. **APIレイヤーのオーバーヘッド幅(実機確認待ち)。** 方針11の通り絶対レートは基準に
   しないが、GUIのローカル連続測定と比べてどれだけ劣化するかは実測する必要がある。
   Step 7 のスナップショット化で `gui_bridge.call()` の往復は3回から1回に減るものの、
   **露光時間の適用待ち(`wait_for_exposure_applied`、`api_mixin.py:272-275`)が
   1リクエストごとに入る点が残る。** 現在の実装はリクエストに `exposure_time_s` が
   含まれていれば、値が前回と同じでも `update_exposure()` を呼んで適用完了を待つ。
   実測で劣化が目立つ場合、**現在値と同じなら適用待ちをスキップする**最適化が
   最も効きそうである(本計画には含めない。実測してから判断する)。
2. **持続的な連続取得のAPI形状。** 「連続測定と同程度の頻度で `/acquire` を叩き続ける」
   という使い方が主要ユースケースとして定着するなら、単発エンドポイントの反復呼び出しは
   本来の形ではなく、GUIの連続測定に相当するストリーミング/連続取得エンドポイントを
   設ける方が筋が良い(リクエストごとのゲート取得・露光適用・スナップショットの
   オーバーヘッドが1回で済む)。本計画のスコープ外だが、課題1の実測結果次第では
   次の検討対象になる。
3. **IPアロウリストと実際のLAN構成の相性。** DHCPでクライアントのIPが変動する環境では
   単一IP指定が破綻する。CIDR対応で緩和されるが、運用実態(固定IPかDHCPか)の確認が必要。
4. **Standby自動開始とWindowsファイアウォール。** アプリ起動のたびに待ち受けを開始するため、
   初回にファイアウォール許可が求められる。実機での挙動確認が必要。
5. **Instrument Status ダイアログがライブ問い合わせを行うか**(表#10)。Step 0 でコードを
   確認して判定する。
6. **`_lock_ui` 冪等化の残存リスク。** Step 1 の手順7で静的に洗い出すが、実機の長時間運用
   でしか出ない経路(温度ポーリング、移動完了コールバック)が残る可能性がある。Standbyの
   実運用開始後、しばらくは「ロックが破れていないか」を意識的に確認する。

## Step 0 実施ログ(2026-09-04)

### 計画の保護対象操作一覧に対する訂正

- **表#4「GUI 分光器Apply」は「取得済」ではなく「なし」だった。** 計画が根拠として挙げた
  `spectrometer_control_mixin.py:143` は `on_calibrate_neon()`(表#5)の中のゲート取得で
  あり、Apply ボタンの `on_apply_spectrometer()` はゲートを一切取っていなかった。
  Apply は物理的な移動を伴う非同期スコープなので、ボタン専用の入口
  `on_apply_spectrometer_clicked()` を新設してそこで取得し、
  `_release_gui_spec_apply_gate()` を `on_spectrometer_moved()` の3分岐すべてに置いた。
  構成ロード経由の `on_apply_spectrometer()` 呼び出しは呼び出し側が既にゲートを持っている
  ため、この新しい入口を通らない(フラグで所有者を区別している)。
- **表#10「Instrument Status 表示」はライブ問い合わせを行う**(オープンな課題5の回答)。
  `InstrumentStatusDialog.refresh()` が `camera_thread.request_status()` と
  `SpectrographStatusWorker` 経由の `spec_ctrl.get_status_snapshot()` を実行する。
  ただしこのダイアログは **モードレス**(`show()` であって `exec()` ではない)ため、
  ダイアログの寿命ではなく **1回の更新サイクル**の間だけゲートを保持する設計とした
  (開いたまま放置されている間ずっとAPIが409になるのを避けるため)。取得できなければ
  更新を開始せず、その旨をステータス行に表示する。
- 表#6 の「単発完了パスが先にゲートを解放する」は計画の記述通りだった。
  `stop_measurement(release_gate=False)` を追加し、背景取得のときだけ保存ダイアログの
  終了まで保持を延長した。Terminate で途中停止した場合は `stop_measurement()` の既定経路が
  `_is_acquiring_bg` を落としてから解放するため、保存ダイアログに到達しないケースでも
  解放漏れにならない。

### 非同期スコープの解放経路表

「サーバー停止」列は方針6により **in-flight を中断しない**ため、いずれの操作も
「該当なし(その操作自身の経路で解放される)」となる。「アプリ終了」はプロセス終了で
ゲート(プロセス内 `threading.Lock`)ごと消える。

| # | 操作 | 取得地点 | 正常完了 | 失敗 | タイムアウト | キャンセル |
|---|---|---|---|---|---|---|
| 1 | GUI 単発取得 | `check_bg_and_take_single` (`file_io_mixin.py`) | `on_data_ready`→`stop_measurement()`、加えて `_process_completed_data` の防御的解放 | `on_acquisition_failed`→`stop_measurement()`+明示解放 | なし(締切なし) | Terminate→`stop_measurement()` / BG不一致で中止→`check_bg_and_take_single` の明示解放 |
| 2 | GUI 連続取得 | `check_bg_and_start_meas` | (無限。Terminate で終了) | `on_acquisition_failed` | なし | Terminate→`stop_measurement()` |
| 3 | GUI Sequential | `check_bg_and_start_seq` | `stop_sequential()`→`stop_measurement()` | `on_acquisition_failed`→`stop_sequential()`+明示解放 | なし | Stop→`stop_sequential()` |
| 4 | GUI 分光器Apply | `on_apply_spectrometer_clicked` (新設) | `on_spectrometer_moved` 成功分岐末尾の `_release_gui_spec_apply_gate()` | 同 失敗分岐 / 起動時例外は `on_apply_spectrometer_clicked` の except | なし | 同 cancelled 分岐 |
| 6 | GUI 背景取得+保存 | `on_acq_bg_clicked` | `_process_completed_data` の `finally`(保存成功・キャンセル・例外すべて) | `on_acquisition_failed` | なし | Terminate→`stop_measurement()`(`_is_acquiring_bg` を落として解放) |
| 8 | GUI 構成ロード | `on_load_configuration`(ダイアログ確定後・移動開始前) | `_finalize_pending_configuration` の `finally`→`_release_gui_config_gate()` | 同上 / 準備段階の例外は `on_load_configuration` の except | なし | `_fail_pending_configuration`(移動キャンセル・移動失敗) |
| 10 | GUI Instrument Status 更新 | `InstrumentStatusDialog.refresh()` | `_finish_if_ready`→`_maybe_release_gate()` | 同上(unavailable スナップショットも同じ経路) | `_on_request_timeout`→pending解放後、ワーカー終了を待って `_on_worker_finished`→`_maybe_release_gate()` | ウィンドウを閉じる→`shutdown()`→`_maybe_release_gate()` |
| 11 | API 取得 | `_api_start_acquire` | `on_data_ready`→`stop_measurement()` | `_api_start_acquire` 内の except(露光適用失敗・`take_single_spectrum` 失敗) | **未対応(Step 3(B) で `_api_abort_acquire` を追加)** | なし |
| 12 | API 構成適用 | `_api_start_configuration_apply` | `api_apply_configuration` / `api_acquire` の明示解放 | `_api_wait_for_configuration` の except / 準備段階の except | `future.add_done_callback`→`_api_release_gate_after_future` | 移動キャンセル→`_fail_pending_configuration`→future 例外→上の失敗経路 |
| 13 | API ハードウェア状態更新 | `_api_begin_hardware_refresh` | `finally`→`_api_end_hardware_refresh` | 同上 | `add_done_callback`→`_api_release_refresh_after_timeout` | なし |

### Step 1 実施ログ — `setEnabled(` 網羅監査の結果

`grep -rn "setEnabled(" src/ui/main_window.py src/ui/ui_mixins/` の全ヒットを分類した。

**修正したもの:**

| 箇所 | 発火契機 | 対応 |
|---|---|---|
| `_close_spectrometer_moving_dialog`(経路1) | API/GUI の構成適用・分光器移動の完了時に必ず | `centralWidget().setEnabled(True)` の直後に `_reassert_ui_lock()` |
| `main_window.py` の `exposure_set_finished` ラムダ(経路2) | APIリクエストのたび(露光指定時) | 名前付き `on_exposure_set_finished()` + ロック考慮 |
| `apply_roi_settings`(経路3) | 表示モード変更・ロック解除・構成ロード | `_sync_roi_widget_states()` へ分離し全 `setEnabled` をロック考慮に |
| `_sync_controls_to_display_mode` の `spin_exc_wl` | **API構成適用で `apply_calibration()` から必ず通る**(計画になかった追加発見) | ロック考慮 |
| `check_spectrometer_changes` の `btn_apply_spec` | 構成適用中の `spin_centre_wl.setValue()` から `valueChanged` 経由(同上) | ロック中は先頭で `False` にして return |
| `stop_measurement` の `btn_single`/`btn_commence` | **API単発取得の完了ごと**(同上) | ロック中は再有効化しない |
| `set_ui_enabled_during_seq` の `btn_start_seq` | ロック中も有効なままだった(`start_sequential()` でしか無効化されない) | ロック対象に追加(`btn_stop_seq` は対象外のまま) |
| `set_ui_enabled_during_seq` の `btn_apply_spec` | 解除のたびに無条件 `True` | `check_spectrometer_changes()` で再計算 |

**修正しないと判断したもの:**

- `on_camera_initialized` / `on_camera_init_failed` の `centralWidget().setEnabled(True)` —
  方針2の除外理由通り。初期化完了時のみ発火し、Standbyの自動待ち受け開始はその後に行う。
  両関数にコメントを残した。
- `take_single_spectrum` / `start_measurement` / `on_acq_bg_clicked` の
  `btn_terminate.setEnabled(True)` — 測定ステートマシンが管理する緊急停止であり、
  ロック考慮にすると **ローカルのSequential測定中に Terminate が押せなくなる**という
  既存挙動の変更になる。現行の locked モードでも同じ状態(API取得中は Terminate が有効)
  であり、意図的な例外として残す。
- `spec_move_cancel_btn.setEnabled` — 移動ダイアログ内のCancelボタン。ロック対象外。
- APIパネル(`btn_start_api` / `btn_stop_api` / `spin_api_port`) — 方針5の通り、
  ロック中もモードを戻せる必要があるため対象外。
- `on_choose_seq_dir` / `main_window.py:738` の `btn_start_seq` — どちらもロック中には
  到達しない(ボタン自体が無効・起動時のみ)。上の `set_ui_enabled_during_seq` 追加により
  解除時の状態も一貫する。
- `toggle_fitting_panel` の `chk_save_fitting` — ロック解除時(`enabled=True`)にしか
  呼ばれない。
- `on_em_gain_info_ready` / `on_em_gain_set_finished` / `on_temperature_set_finished` —
  既にロック考慮済み。

**残存リスク(オープンな課題6):** 静的な洗い出しでは、温度ポーリングや移動完了
コールバックのように実機の長時間運用でしか出ない経路が残る可能性がある。
`tests/test_ui_lock_reasons.py` に経路1-3の回帰テストを置いた。

### Step 0 の自動確認

`--debug` の SpectrometerGUI を offscreen で起動し、以下16項目を確認した(すべて PASS):
背景ロードの保持/解放/多重取得拒否/例外時解放、背景取得の保存ダイアログ内保持・キャンセル
解放・Terminate 中断時解放、`POST /calibration` の保持/解放/busy時 `GateBusyError`
(reason=`local_operator_action`)、GUI分光器Apply の取得と移動完了後の解放。

---

## 実装完了ログ(2026-09-05)

Step 0 → 1 → 2 → 7a → 3 → 4 → 5 → 6 → 7b → 8 の推奨順で全ステップを実装した。

### 計画からの主な変更点

- **`GateBusyError` は `detail` 属性を持つ。** 当初は `args[0]` に dict を入れる想定だったが、
  それだと `str(e)` が dict の repr になり、`RuntimeError` としてメッセージを読む既存の
  テスト/ログが壊れる。人間可読なメッセージ + `detail` 属性の2本立てにした。
- **`SpectrometerGUI` が自分で `GuiBridge` を生成するようにした**(`main.py` から外部注入する
  形をやめた)。Standbyの自動待ち受け開始はカメラ初期化ハンドラから走るため、
  「呼び出し側が後から bridge を代入してくれている」ことに依存していると自動起動が失敗する。
- **`load_clients()` は3要素タプルを返す**(`clients, needs_save, migrated_legacy`)。
  「旧キーファイルが読めなかった」場合に旧ファイルを削除しないため。
  読めないファイルでも操作者が手で復旧できる唯一のキーである可能性がある。
- **`ACQUIRE_TIMEOUT_MARGIN_S` を定数に切り出した**(元は `+ 15.0` のリテラル)。
  タイムアウト清掃のテストが毎回15秒待つのを避けるため。
- **`expected_state_token` は `POST /acquire` 系のみ**に追加した。
  `POST /configurations/{id}/apply` への追加は計画に無いため見送り
  (内部の `_api_wait_for_configuration` は引数を受け取れる状態にしてある)。
- `scripts/_dev_run_api.py` は既に壊れている(存在しない `create_app(..., api_key=...)` を
  呼んでいる)。本計画の範囲外なので触っていない。削除候補。
  → コードレビューで再指摘されたため削除した(2026-09-06)。自身のdocstringに
  「Step 8が終われば削除してよい」と明記されており、Step 8は完了済み。
  後継は `scripts/_dev_api_load.py` とStandbyモードのGUIパネル。

### 検証結果

- `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests`: 400 tests。
  唯一の失敗 `test_configuration_manager_dialog.test_table_has_checkbox_column_and_expected_columns`
  は**本計画の着手前から失敗していた**もの(列見出しが "Centre" vs "Centre (nm)")で、無関係。
- **Standby E2E(実サーバー、`--debug`、25項目すべて PASS):** Standbyでの200応答、未知キーで401、
  `/docs`と`/openapi.json`が404、リクエスト間はUI操作可能、リクエスト中のロックと
  "Remote control active" 点灯、デバウンス中のロック維持と約1秒後の解除、ゲート解放、
  tokenの往復と不一致時409(detailに現在値)、許可外IPで403・許可IPで200、
  Sequential中の409(`reason=local_sequential_run`)、Off切替後の受付停止。
- **Lockedモードの回帰(18項目すべて PASS):** 単発/連続/Sequentialのゲート取得と解放、
  Locked起動中の測定系コントロール無効化、APIパネルのみ操作可能、
  移動ダイアログclose後もロック維持(経路1)、`exposure_set_finished` 後もロック維持(経路2)、
  Off復帰後の全復元。
- **連続実行(`scripts/_dev_api_load.py`、closed-loop 20秒):** 179リクエストすべて200、
  8.9 req/s、latency median 112 ms / p90 123 ms。**50ms間隔のサンプリングでロック状態の
  トグルは全実行を通じて1回のみ**(デバウンス無しなら約358回)。実行後はロック解除・ゲート解放を確認。
  ※ `--debug` の合成スペクトルでの値なので、絶対値は実機と異なる(方針11)。

### オープンな課題の更新

- **課題5(Instrument Status のライブ問い合わせ)→ 解決。** 上記 Step 0 実施ログを参照。
- 課題1(APIレイヤーのオーバーヘッド幅)、課題3(IPアロウリストとDHCP)、
  課題4(Windowsファイアウォール)、課題6(ロック破りの残存リスク)は
  いずれも実機確認待ちのまま。
- `TODO(実機確認待ち)`: 長時間積算中のタイムアウト清掃でカメラが正しく停止するか、
  別マシンからLAN越しの許可IP判定、Windowsでのファイアウォール許可ダイアログ、
  GUIのローカル連続測定とAPI経由のスループット比較(方針11の相対評価)。
