# Local Raw Studio (Revelado Local)

A local application for comparing color profiles, organizing libraries, and developing RAW photographs using darktable. The local vision-language model only proposes bounded numerical parameters for darktable modules. There is no generative AI hallucination, image reconstruction, or inpainting.

## Installation & Setup

### Prerequisites
- Python 3.10+
- [darktable](https://www.darktable.org/) with experimental `darktable-mcp` binary support (can be configured via `DARKTABLE_MCP` environment variable if not in PATH).

### 1. Install Dependencies
Create and activate your preferred Python environment (venv, conda, etc.), then install requirements:

```bash
pip install -r requirements.txt
```

### 2. Launch the Application
- **Using the PowerShell launcher (Windows):**
  ```powershell
  ./run.ps1
  ```
- **Or directly with Uvicorn:**
  ```bash
  python -m uvicorn studio.app:app --host 127.0.0.1 --port 8765
  ```

Once started, open your web browser at `http://127.0.0.1:8765`.

> [!NOTE]
> darktable communicates over stdio JSON-RPC using an in-memory library and `write_sidecar_files=never`. Your original RAW files and sidecars are never modified.

## Core Features

### 1. Photo Library & Organization
- **Visual File Browser**: Import RAW photos (`.ARW`, `.CR2`, `.NEF`, `.DNG`, etc.) directly from any connected drive or folder with fast embedded thumbnail extraction.
- **Folders & Organization**: Detects original source directories automatically (e.g. `OSAKA`, `KIOTO`, `FUJI`) and allows custom folder assignment.
- **Tagging**: Add and manage `#tags` per image or in batch. Click any tag chip to instantly filter the library.
- **Favorites**: Star favorite photos with `★` and toggle favorites filtering with a single click.
- **Search & Filter**: Real-time search across filenames, tags, and folder collections.
- **Multi-Selection & Batch Actions**: Enter selection mode with `Select` in the filter bar to batch-tag, move to folder, or safely remove multiple photos.
- **Expanded Grid View**: Click **⛶ Expand** to view a responsive multi-column gallery of thumbnails.

### 2. Developing & Profile Comparison
- **Color Profiles & Multi-Select**: Compare classic film simulations and creative looks (Portra, Tri-X, Astia, Classic Chrome, CineStill, Kodachrome, Neutral, and more). Select one or multiple profiles simultaneously for batch processing.
- **Prompt-Based AI Developing**: Select `00_PROMPT_IA` or pick style chips (e.g. *Cinematic warm night*, *Moody rainy day*, *Golden hour editorial*) to describe the aesthetic in natural language.
- **Side-by-Side Comparison**: Synchronized split viewer comparing the base develop against any developed look.
- **Smooth Navigation & Zoom**:
  - Zoom controls: `Fit`, `−`, `+`, `100%`, `Full Page` (or press `F` / `Esc`).
  - Cursor-centered wheel zoom and drag-to-pan synchronized across both viewports.
  - Right-click on any developed image to copy it directly to your clipboard.
  - Smooth horizontal scrolling on developed profiles.
- **High-Resolution Export**: Export finished developments up to 6000px PNG in `PROCCESED/PERFILES/<profile>/studio_<image>/` with full recipe metadata.

## Local Vision Model & Supervision

The system integrates `Qwen3-VL-4B-Instruct Q4_K_M` running locally on GPU via `llama.cpp`. Download and start the model daemon using the provided helper scripts:

```powershell
./scripts/download-model.ps1
./scripts/run-model.ps1
```

An automatic supervisor (`ensure_server()`) continuously checks model availability on `http://127.0.0.1:8081/v1`.

### Numerical Contract & Safety Guardrails
The model receives a downscaled image preview, photographic telemetry (luminance, white clipping, color cast, and skin tone detection), and the base recipe. It responds with a structured JSON proposal bounded by strict limits (`LIMITS` in `studio/recipes.py`):
- All proposed adjustments are clamped to safe photography ranges.
- Geometry and framing are preserved; modules modifying perspective or crop are prohibited.
- Module parameters are strictly validated before dispatching to darktable over JSON-RPC.

## Advanced Photographic Adaptation

The adaptation engine (`studio/adaptation.py`) evaluates scene characteristics before proposing and applying adjustments:

- **Per-Scene White Balance**: Detects chromatic casts in neutral midtones and subtly compensates temperature (`temp_bias`) and tint (`tint_bias`) without flattening intentional creative lighting.
- **Skin Tone Protection**: Identifies human skin regions using HSV and YCbCr color segmentation. When skin tones are present, aggressive contrast and saturation boosts are constrained to keep portrait skin natural.
- **Highlight & Roll-Off Protection**: Monitors high luminosity percentiles (P98 and P99.5). In overexposure-risk scenes, it tunes highlight roll-off in the sigmoid module (`sig_highlight_rolloff`) and attenuates exposure.
- **Adaptive Sharpness & Denoise**: Computes high-frequency noise variance (`noise_sigma`). In high-ISO or grainy images, it raises sharpen thresholds and bilateral noise reduction to avoid grain amplification.

## Evaluation Suite

To evaluate model decisions against fixed profiles and algorithmic baselines, a dedicated evaluation script is included:

```powershell
python ./scripts/evaluate-model.py --profile 09_PORTRA_WARM
```

The script benchmarks three parallel stages (Fixed Profile, Algorithmic Adaptation, and Qwen3-VL Model), measuring:
- Highlight blowout rate (white clipping > 99.5%).
- Shadow crushing rate (black clipping < 0.5%).
- Effective dynamic range (EV).
- Skin tone harmony score (0–100).

Results are exported into interactive reports with side-by-side thumbnails and histograms at `.studio/reports/evaluation_report.html` and `.studio/reports/evaluation_summary.json`.

## Safety & Data Integrity

- **Original RAW and XMP files are never modified or deleted.**
- All processing is 100% local; no image data or telemetry ever leaves your machine.
- All recipes are non-destructive, reproducible JSON parameter sets.

## Roadmap & Future Additions

Planned features and enhancements for upcoming releases:

- **Interactive Local Masking & Zone Selection**:
  - **Manual Zone Selection**: Ability to select, brush, or define radial/linear gradients over specific zones of the photo (e.g., subject, face, sky, foreground) to apply targeted exposure, contrast, and color grading locally.
  - **AI Semantic Segmentation**: Automated subject, sky, and skin tone detection so the local model can direct adjustments to specific regions while keeping the rest balanced.
- **Model Improvements & Fine-Tuning**:
  - **Domain Fine-Tuning**: Training and fine-tuning the vision-language model on master darkroom datasets, film stocks, and professional color grading corpora for deeper aesthetic precision.
  - **Multi-Turn Conversational Refinement**: Iteratively refine develops through conversational prompts (e.g. *"lift shadows by 0.5 EV and add a subtle cyan bias to the highlights"*).
  - **Inference Optimization**: Context caching, weight quantization, and faster token generation to minimize development latency on local GPUs and Apple Silicon/DirectML.
- **Workflow & Color Science Expansions**:
  - **3D LUT Export (`.cube`)**: Export developed looks as standard 3D LUTs for use in video editing suites (DaVinci Resolve, Premiere Pro, Final Cut).
  - **External RAW Editor Sidecars**: Export recipes as compatible Lightroom and Capture One sidecar files (`.xmp`).
  - **Custom Profile Creation**: Upload reference photographs or color palettes to extract and save reusable custom profiles.
  - **Multi-Photo Batch Developing**: Apply profiles, prompts, or model adaptations across multiple selected library images in a single batch operation.

## Testing

Run the automated test suite with pytest:

```powershell
python -m pytest tests -v
```

Tests verify the local model supervisor, photographic adaptation engine, library database migrations, safety contract bounds, and path traversal isolation.

---
See [CHANGELOG.md](file:///d:/FOTOS/revelado-local/CHANGELOG.md) for version history and updates.
