# H3 Prompt Studio：初回インストールとシリーズ制作

このガイドは Windows 用の**カスタマイズ版**です。上流の GitHub リポジトリだけを新たにクローンしても、この環境で追加された脚本管理・カードライブラリ・映像結合機能は含まれません。Studio、ComfyUI、Ollama／LM Studio は別々のアプリです。Studio のインストールだけではモデルや ComfyUI は導入されません。

## 自分で用意するもの

| 項目 | 用途 | 確認 |
| --- | --- | --- |
| Windows 10/11、十分な空き容量 | アプリ、モデル、完成映像 | `D:\h3tool` は配置例です。 |
| `uv` | アプリ専用 Python 3.12 `.venv` の作成 | `uv --version`。システム全体の Python は不要です。 |
| Node.js 22.12 以上（20.19 以上も可）と npm | 初回の画面ビルド | `node -v`、`npm -v`。 |
| FFmpeg、FFprobe（PATH に登録） | 映像の結合。セットアップ時にも検査 | `ffmpeg -version`、`ffprobe -version`。 |
| ComfyUI、互換 H3 モデル／カスタムノード | 動画生成 | ComfyUI は別途起動し、Studio のローカル接続先に設定します。 |
| Ollama **または** LM Studio とローカル LLM | AI 脚本計画・プロンプト | 接続設定でインストール済みモデルを選びます。手動編集は LLM なしでも可能です。 |
| Krea 2 または Z-Image モデル | カード画像／キーフレームの任意生成 | 自分の画像をアップロードするだけなら不要です。 |

内蔵 H3 参照画像フローは `MiniMaxH3ReferenceToVideo`、`MiniMaxH3SigmaShift`、`H3SLAAttention`、`SaveVideo` などのノードを確認します。代表的なモデルは `minimax_h3_ref2va_pruned_int8_convrot.safetensors`、`qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors`、`minimax_h3_video_vae_fp16.safetensors`、`minimax_h3_audio_vae_fp32.safetensors`、`minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors` です。別の H3 モードや任意の LoRA 配合には追加ファイルが必要です。最終的には Studio 内のモデル／ワークフロー検査結果に従ってください。任意の Krea 2 画像生成には `krea2_turbo_bf16.safetensors`、`qwen3vl_4b_bf16.safetensors`、`qwen_image_vae.safetensors` と対応ノードが必要です。モデルは信頼でき、利用権限のある提供元から入手してください。

## 配布用のコピー

このカスタマイズ版のソースと起動スクリプトをコピーします。ただし `data/`、`logs/`、`.venv/`、`frontend/node_modules/`、`dist/`、キャッシュ、秘密情報のあるファイルは含めないでください。個人の脚本、画像、音声、動画を含む場合があります。自分の別 PC へ作品を移すなら `data/` を別途バックアップします。参考元の `D:\xiangmu\AI视频制作` も配布物に含めません。

## 初回インストール

1. 上記の `uv`、Node.js と npm、FFmpeg／FFprobe を導入し、PowerShell を開き直します。
2. カスタマイズ版を独立したフォルダーに置き、`Install-H3.bat` をダブルクリックします。Python 環境、uv/npm キャッシュ、フロントエンド依存関係はアプリ自身のフォルダーに作られ、既存の `data/` は保持されます。

3. インストール後は `H3-Start.bat` をダブルクリックします。コンソールを表示したまま起動する場合は `H3-Start-Visible.bat`、停止には `H3-Stop.bat` を使います。ブラウザーで `http://127.0.0.1:8766/` を開きます。ComfyUI と Ollama／LM Studio は別途起動してください。
4. Studio の接続設定でローカル LLM と ComfyUI の実際のアドレスを選び、モデル検査を更新します。画像生成を使わない場合は、画像生成設定をオフのままにできます。

更新時は `data/` を消さないでください。動画ジョブの完了後、`powershell -NoProfile -ExecutionPolicy Bypass -File .\docs\Restart-H3-When-Idle.ps1` で Studio のみ安全に再起動できます。ブラウザーが旧画面なら `Ctrl+F5` を試します。起動失敗時のログは `logs/` にあります。

## 作品と完成映像

1. 「キャラクター資料庫」で共通カードセットを作り、各「エピソード／クリップ」のカードに明示的に適用します。共有セットへの同期も明示操作です。
2. 長い話は複数の作品パートに分け、各パートで脚本、カット、採用テイクを確認します。
3. 「脚本管理」で話数とパート順を保存します。完成した話から「この話を結合」できます。2 話以上をチェックすれば「選択した話を結合」、全話が揃えば「全編を結合」できます。
4. 完成映像は PC に直接保存されます。「保存場所を開く」でエクスプローラーを開いてください。ブラウザーからのコピー保存は任意です。不足したパートを別作品で自動補充しません。

パート内の合片は `data/production_films/<作品 ID>/`、脚本合片は `data/series_films/<脚本 ID>/` に保存されます。脚本フォルダー内の `episodes/` は単話、`selections/` は選択話、`<バージョン署名>/complete.mp4` は全編です。採用テイクが変わると新しい版を作り、旧版を上書きしません。作業ファイルを手動で移動・改名せず、共有にはコピーを使ってください。大きな更新前には `data/` をバックアップしてください。
