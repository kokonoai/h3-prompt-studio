# H3 Prompt Studio：首次安裝與系列製作

本指南適用於 Windows 上的**客製版**。單獨重新複製上游 GitHub 專案，不會自動取得本機新增的劇本管理、卡庫與影片合併功能。Studio、ComfyUI、Ollama／LM Studio 是不同程式；安裝 Studio 不會順便安裝模型或 ComfyUI。

## 需要自行準備

| 項目 | 用途 | 檢查方式 |
| --- | --- | --- |
| Windows 10/11、足夠磁碟空間 | 程式、模型與成片 | `D:\h3tool` 只是安裝路徑範例。 |
| `uv` | 建立本程式專用 Python 3.12 `.venv` | `uv --version`；不必另外安裝全域 Python。 |
| Node.js 22.12+（或 20.19+）及 npm | 首次建置介面 | `node -v`、`npm -v`。 |
| FFmpeg 與 FFprobe，均加入 PATH | 影片合併，安裝程式也會檢查 | `ffmpeg -version`、`ffprobe -version`。 |
| ComfyUI、相容的 H3 模型與自訂節點 | 影片生成 | 另外安裝及啟動；Studio 僅連接本機 API。 |
| Ollama **或** LM Studio，以及本地 LLM | AI 劇本規劃與提示詞 | 在連線設定選已安裝的模型；手動編輯可不使用 LLM。 |
| Krea 2 或 Z-Image 模型 | 選用的卡圖／關鍵影格生圖 | 只上傳自己的圖片就不需要。 |

內建 H3 參考圖流程會檢查 `MiniMaxH3ReferenceToVideo`、`MiniMaxH3SigmaShift`、`H3SLAAttention`、`SaveVideo` 等節點。常用模型包含 `minimax_h3_ref2va_pruned_int8_convrot.safetensors`、`qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors`、`minimax_h3_video_vae_fp16.safetensors`、`minimax_h3_audio_vae_fp32.safetensors`、`minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors`。其他 H3 模式或選用的 8 步 LoRA 配方會有額外需求；請以 Studio 實際的模型／工作流程偵測為準。選用的 Krea 2 生圖需要 `krea2_turbo_bf16.safetensors`、`qwen3vl_4b_bf16.safetensors`、`qwen_image_vae.safetensors` 與相容節點。請從可信且有使用權限的來源取得模型與節點。

## 給別台電腦的乾淨安裝包

複製**目前客製版**原始碼和啟動腳本；不要夾帶 `data/`、`logs/`、`.venv/`、`frontend/node_modules/`、`dist/`、快取或含金鑰的檔案。這些可能包含私人劇本、圖片、聲音和影片。若是搬移自己的作品，請另外備份 `data/`，不要混進給別人的安裝包。參考用的 `D:\xiangmu\AI视频制作` 也不應放入。

## 第一次安裝

1. 安裝上述 `uv`、Node.js 與 npm、FFmpeg／FFprobe，然後重新開啟 PowerShell。
2. 把客製版放入獨立資料夾，直接按兩下 `Install-H3.bat`。安裝器會把 Python 環境、uv/npm 快取與前端相依套件放在軟體自己的資料夾內，並保留既有 `data/`。

3. 安裝後按兩下 `H3-Start.bat`；要保留即時服務視窗時使用 `H3-Start-Visible.bat`，停止服務使用 `H3-Stop.bat`。在瀏覽器開啟 `http://127.0.0.1:8766/`。Studio 不會自動啟動 ComfyUI 或 Ollama／LM Studio。
4. 在 Studio 的連線設定選本地 LLM 與 ComfyUI 的實際網址，重新整理模型偵測。若不使用生圖，可維持生圖功能關閉。

更新時**不要刪除 `data/`**。等影片任務完成後，可執行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\docs\Restart-H3-When-Idle.ps1`，只安全重啟 Studio。頁面顯示舊版時按 `Ctrl+F5`；啟動問題可檢查 `logs/`。

## 劇本、劇集與成片

1. 在「角色資料庫」建立共用卡組，明確套用至每個「劇集／片段」專案。同步回共用卡組也是獨立操作，不會自動覆蓋。
2. 長劇集可分成多個按順序排列的製作片段，各自規劃分鏡、生成影片並選好採用版本。
3. 到「劇本管理」保存集數與片段順序。完成一集即可「合併本集」；勾選至少兩集可「合併選中集」；所有集完成後再「合併全劇」。
4. 成片直接保存在本機，按「開啟檔案位置」即可在檔案總管查看；瀏覽器「另存副本」只是選用。缺少的片段不會用別的專案補位。

片段內分鏡合片保存在 `data/production_films/<專案 ID>/`。劇本成片保存在 `data/series_films/<劇本 ID>/`，其中 `episodes/` 是單集、`selections/` 是選集合併、`<目前版本簽章>/complete.mp4` 是全劇。更改採用版本後會產生新簽章，不會覆蓋舊成片。請勿手動搬移或更名程式仍在使用的結果；分享時另存副本。重大更新前先備份 `data/`。
