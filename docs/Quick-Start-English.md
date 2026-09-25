# H3 Prompt Studio: Quick Install and Series Workflow

This is a local Windows workflow for this customized H3 Prompt Studio. Installing the app does **not** install ComfyUI, H3 models, Ollama, or LM Studio. A fresh clone of the upstream repository alone will not contain the local script-management, card-library, and film-assembly additions.

## Prepare these separately

| Component | Needed for | Check |
| --- | --- | --- |
| Windows 10/11 and free disk space | Application, models and films | Keep space for video output; `D:\h3tool` is only an example location. |
| `uv` | Private Python 3.12 environment | `uv --version`; no global Python installation is required. |
| Node.js 22.12+ (or 20.19+) and npm | First-time frontend build | `node -v`, `npm -v`. |
| FFmpeg and FFprobe on PATH | Film assembly; also checked by Setup | `ffmpeg -version`, `ffprobe -version`. |
| ComfyUI with compatible H3 models and custom nodes | Video generation | Run ComfyUI separately; Studio connects to its local API. |
| Ollama **or** LM Studio with a local LLM | AI script planning and prompts | Pick an installed model in Studio's connection settings. Manual editing can work without it. |
| Krea 2 or Z-Image model | Optional card/keyframe images | Uploaded images work without an image generator. |

The built-in H3 reference workflow checks for nodes such as `MiniMaxH3ReferenceToVideo`, `MiniMaxH3SigmaShift`, `H3SLAAttention`, and `SaveVideo`. Its common model files include `minimax_h3_ref2va_pruned_int8_convrot.safetensors`, `qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors`, `minimax_h3_video_vae_fp16.safetensors`, `minimax_h3_audio_vae_fp32.safetensors`, and `minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors`. Other H3 modes and the optional eight-step LoRA recipe have their own requirements. Use Studio's live model/workflow check as the final authority. Optional Krea 2 images require `krea2_turbo_bf16.safetensors`, `qwen3vl_4b_bf16.safetensors`, `qwen_image_vae.safetensors`, and compatible ComfyUI nodes. Obtain models and nodes from sources you trust and are permitted to use.

## Share a clean copy

Copy the **customized** app source and launch scripts, but exclude `data/`, `logs/`, `.venv/`, `frontend/node_modules/`, `dist/`, caches, and files containing secrets. These can contain private scripts, images, voice samples, or videos. Back up `data/` separately if moving your own projects; do not include it in a clean package for someone else. The scripts use their own directory as the app root; `D:\h3tool` is an example, not a hard requirement.

## First install

Install `uv`, Node.js 22.12+ (20.19+ also works) with npm, and FFmpeg with both `ffmpeg.exe` and `ffprobe.exe` available on PATH. Reopen your terminal, then double-click `Install-H3.bat`. It keeps Python, uv/npm caches, and frontend dependencies inside the application directory while retaining an existing `data/` folder.

After installation, double-click `H3-Start.bat`. Use `H3-Start-Visible.bat` when you want a visible server console and `H3-Stop.bat` to stop it. Open `http://127.0.0.1:8766/`. Studio does not start ComfyUI, Ollama, or LM Studio; start the services you use separately. Do not delete `data/` during updates.

After a backend update, wait for all renders to finish, then run `powershell -NoProfile -ExecutionPolicy Bypass -File .\docs\Restart-H3-When-Idle.ps1`. The script checks the port, exact H3 process, and active video jobs; it refuses to stop anything while a render is active.

## One story across ten episodes and multiple projects

1. In **Character library**, create or choose a shared card set. In each **Episodes / Clips** part, use **Merge into project** to load it. Cards can include character, wardrobe, prop, environment, overview images, and bound voice samples.
2. Split a long episode into ordered project parts, e.g. `Episode 01 - Part 01` and `Episode 01 - Part 02`. Each project plans and renders its own clips.
3. When a project adds or improves cards, click **Sync shared card set**. **Save library** saves only that project. In the next project, merge the updated set. Sync retains cards and assets from other projects; later nonempty fields on a matching card take priority, so review conflicting edits.
4. Open **Script management**, create a script with your episode count, link the shared character records, add parts in viewing order, and save. Linking the set does not silently overwrite part cards.
5. Once a project's adopted takes are ready, its storyboard film is saved locally. In **Script management**, you can **Assemble episode** without waiting for the rest, select at least two episodes and **Assemble selected**, or **Assemble full series** when every episode is ready. Missing clips are never replaced by unrelated outputs. Each result has **Open file location**; browser download is optional.

## Where films are saved

Project storyboard films live under `data/production_films/<project ID>/`. Script films live under `data/series_films/<script ID>/`: `episodes/` for single episodes, `selections/` for chosen episodes, and `<current version signature>/complete.mp4` for the full series. A new adopted take creates a new signature without overwriting the previous result. The page shows the exact absolute path and opens it in Explorer. Do not move or rename the working files inside these folders; use **Download a copy** when sharing. Original ComfyUI renders and project parts remain available.

Series archiving only archives the grouping. It leaves projects, card sets, and videos intact. Back up `data/` before major upgrades.

## Review and optional keyframes

The video output page lists each clip's speaker and dialogue beside its video. Review identity, voice, lip sync, and continuity; regenerate only the clips that need it.

**Automatic environment keyframes** are off by default. Enable them in **Settings** only if your local ComfyUI has a compatible image model. During one-click production, the app suggests up to three background plates for new locations lacking environment references or clip keyframes. These are planning context, not extra H3 reference slots. Preview suggestions first. Image generation uses local VRAM; if it fails, the one-click sequence stops before video submission. You can leave it off and add clip keyframes manually.

If the page is blank, hard-refresh with `Ctrl+F5`, verify port 8766 belongs to this Studio, and inspect `logs/`. If ComfyUI times out, wait for its active render and check the configured local address. For truncated LLM plans, split long source text by episode or scene and use a local model with sufficient context.
