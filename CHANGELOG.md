# Changelog

All notable changes to the Raw Studio (Revelado Local) project are documented in this file.

## [Unreleased] - 2026-09-06

### 1. English Localization
- **User Interface**: Translated all interface elements, headings, labels, button texts, empty state, and dialog prompts into English.
- **Engine & Model Status**: Local model connection messages, Darktable bridge messages, and progress states now report in English.
- **Error & Validation Messages**: Backend exception strings across `app.py`, `library.py`, and `recipes.py` translated to English.
- **Profile Catalog**: Cleaned up encoding and updated all 8 color profile titles and descriptions to clear English.

### 2. Left Photo Library Enhancements
- **Internal Sidebar Scrolling**: The library now has an independent scrollable container (`overflow-y: auto`) with styled scrollbars, allowing smooth navigation through any number of imported photos without disrupting the workspace layout.
- **Expand / Grid View Mode**: Added an **Expand** (`⛶ Expand` / `⊟ Collapse`) toggle button in the library header:
  - Widens the library panel to 480px.
  - Switches photo listings from single-column items into a responsive multi-column photo grid.
  - Enlarges thumbnail cards for comfortable browsing of large photo sets.
- **Active State Indicators**: Improved active border and highlight styling for the currently selected photo.

### 3. Interactive Multi-Level Photo Zoom & Pan
- **Continuous Arbitrary Zoom**: Supports continuous scaling from 10% up to 500% with GPU-accelerated transforms (`translate3d` & `scale`).
- **Toolbar Zoom Controls**:
  - `Fit`: Automatically scales and centers the photo to fit the viewport.
  - `−` (Zoom Out): Decrements zoom level smoothly.
  - Zoom Indicator: Displays current zoom percentage (e.g. `Fit`, `100%`, `150%`).
  - `+` (Zoom In): Increments zoom level smoothly.
  - `100%`: Instantly jumps to 1:1 pixel mapping (1 image pixel = 1 screen pixel).
- **Mouse Wheel Zoom Anchored at Point**:
  - Scrolling the mouse wheel over either viewport zooms into or out of the exact pixel beneath the cursor.
- **Click & Drag Panning**:
  - When zoomed in, clicking and dragging smoothly pans the view with visual `grab`/`grabbing` feedback.
- **Synchronized Comparison**:
  - Both the "Base Develop" and "Profile" viewports pan and zoom in lockstep, making side-by-side inspection of fine details effortless.
- **Double-Click Toggle**:
  - Double-clicking on any point in the image instantly zooms to 100% centered at that point (or returns to `Fit` if already zoomed).
- **Keyboard Shortcuts**:
  - `+` or `=`: Zoom in
  - `-` or `_`: Zoom out
  - `0`: Fit to screen
  - `1`: 100% actual size

### 4. Repository Relocation & Path Auto-Repair
- **Database & Recipe Path Migration**: Fixed stale paths pointing to the previous repository location (`D:\FOTOS\FOTOS JAPON\.studio\...`). Migrated 20 image copies in `library.sqlite` and 43 render preview paths in `recipe.json` to the current `.studio` location.
- **Dynamic Path Auto-Healing**: Updated `studio/library.py` and `studio/app.py` so that if `image['copy']` or `recipe['preview']` does not exist at its saved absolute path (e.g. after moving the repository folder), it automatically checks and resolves against `STATE / 'originals'` and `STATE / 'renders'`, updating the database automatically.

### 5. Workspace Spacing & Developed Profile Cards
- **Expanded Profile Results**: Enlarged the developed profile result thumbnails at the bottom from 130px to 210px+ cards with enhanced preview images, clear title labels, hover elevation, and an accent border for the active profile selection.
- **Breathing Space**: Added generous margins under the top navigation header and between the development desk toolbar and the image comparison viewer.

### 6. Expanded Profile Catalog (12 New Color & Tonal Profiles)
Expanded the catalog from 8 to 20 carefully calibrated photographic profiles:
- `09_PORTRA_WARM` (**Warm Portra**): Flattering skin tones with warm highlights and gentle micro-contrast inspired by Kodak Portra 400.
- `10_CLASSIC_CHROME` (**Classic Chrome**): Subdued saturation with deep midtone contrast and muted skies inspired by Fujifilm Classic Chrome.
- `11_GOLDEN_HOUR` (**Golden Hour**): Amber, honey midtones and warm sunset glow with soft shadow transitions.
- `12_KODACHROME` (**Vintage Kodachrome**): High-contrast 1960s slide film look with punchy primary reds and rich blues.
- `13_NORDIC_COOL` (**Nordic Cool**): Clean cool whites, muted foliage, and enhanced cyan-blue atmospheric clarity.
- `14_MOODY_FOREST` (**Moody Forest**): Deep emerald foliage with yellow suppression and lifted shadows for woods and rainy landscapes.
- `15_URBAN_CYBER` (**Urban Cyberpunk**): Vibrant night city aesthetic featuring saturated neon cyan shadows and sodium amber highlights.
- `16_MONO_FINE_ART` (**Silver Fine Art B&W**): Silky zone-system monochrome with deep blacks and radiant specular highlights.
- `17_FADED_FILM` (**Faded Matte Film**): Raised black floor, warm shadow undertones, and gentle contrast for an authentic indie 35mm feel.
- `18_PACIFIC_AQUA` (**Pacific Ocean Aqua**): Vibrant turquoise-cyan ocean tones and warm golden sand hues for coastlines and water scenes.
- `19_HIGH_KEY` (**Clean High-Key**): Luminous, open presentation with clean whites and subtle vibrance for modern commercial portraits and still life.
- `20_STREET_TRI_X` (**Tri-X Street B&W**): High-contrast documentary monochrome with sharp edge definition inspired by Kodak Tri-X 400.

### 7. Clear Cache, Multi-Profile Selection & Batch Progress
- **Clear Developed Photos Cache**: Added a dedicated `Clear developed cache` button and `/api/renders/clear` endpoint to purge all cached preview files in `.studio/renders` and clear developed photos from the workspace. Fixed route handling for both POST and GET to ensure instantaneous clearing.
- **Clean Profile Selection**: Removed inline descriptions from the multi-select options and main view for a clean, compact interface.
- **Profile Guide Modal (`ℹ`)**: Added a dedicated info button next to the profile selector that opens a popup guide with full descriptions and best use-cases for all 20 profiles on demand.
- **Batch Progress & Remaining Counter**: When developing multiple profiles at once, the loading and status text displays real-time step progress with remaining and total counts:
  - Example: `[2/5] (3 remaining) Classic Chrome — Developing with darktable` / `[2/5] (3 remaining) Classic Chrome — Preparing image`.

### 8. Enhanced Import Photos Dialog & Preview Engine
- **Arbitrary Folder & Drive Selection**:
  - **Drive Quick Access**: Detects and displays all available system drives (e.g. `C:\`, `D:\`) as clickable chips.
  - **Path Bar & Manual Input**: Editable text path bar with `Go` button and Enter-key support to paste or type any folder path.
  - **Folder Navigation**: `Up` parent button and subfolder pills `[folder] /` to easily drill down or navigate up directories.
  - **Configurable Default Folder**: Retains a primary default photo folder (`Main Folder`) with a quick button to return anytime, plus a `Set as Main` button that persists the user's chosen folder in `.studio/config.json`. Clean text UI without emojis.

- **Fast Visual Photo Previews (Thumbnails)**:
  - Responsive visual grid (`.browser-grid`) displaying thumbnail cards for all supported photos in the selected directory.
  - Ultra-fast preview engine (`/api/browse/thumbnail`): extracts embedded camera JPEG previews from RAW files (`.ARW`, `.CR2`, `.NEF`, etc.) in ~15ms with automatic EXIF orientation, caching thumbnails in `.studio/cache/browser_thumbs/` for instant subsequent loads.
  - Lazy loading (`loading="lazy"`) to ensure smooth scrolling even in folders with hundreds of high-resolution images.
  - Cards display visual thumbnail, format badge (`[RAW]` in amber vs `[JPG]`/`[PNG]`), truncated filename, and formatted file size (e.g. `24.5 MB`).
- **Batch Selection Controls**:
  - **Select All**: Instantly checks all photos in the current directory (respecting any active search filter).
  - **Select All RAW**: Smart filter that selects only camera RAW files (`.arw`, `.cr2`, `.cr3`, `.nef`, `.dng`, `.raf`, `.rw2`, etc.), excluding JPEGs or non-RAW files.
  - **Clear**: Deselects all files in the current folder.
  - **Card Toggle**: Clicking anywhere on a photo card toggles its selection.
  - **Modal Dismissal Fix**: Added `dialog:not([open]) { display: none !important; }` and restricted `.browser-dialog[open] { display: flex; }` so that closing the import dialog properly hides it completely from the main workspace rather than remaining rendered in-flow.

### 9. Library Photo Deletion (Single & Multiple)
- **Single Photo Quick Deletion**:
  - Hovering over any photo card in the left library displays a subtle `✕` remove button.
  - Clicking `✕` prompts for confirmation before removing the photo from the library.
- **Multi-Selection Mode & Batch Deletion**:
  - Added a **`Select`** toggle button in the library header (`LIBRARY` sidebar).
  - Clicking **`Select`** activates selection mode, revealing checkboxes on all photo thumbnails and displaying a batch action bar:
    - **`All`**: Selects all imported photos in the library.
    - **`Clear`**: Clears all selected checkboxes.
    - **`Cancel`**: Exits selection mode.
    - **`Delete (N)`**: Destructive action button that prompts for confirmation and removes all selected photos in one batch.
- **Safe Data Policy**:
  - Deleting photos removes their verified copies in `.studio/originals/`, generated previews in `.studio/previews/`, developed renders in `.studio/renders/`, and database records.
  - **Your original RAW camera files on your disk or SD card are NEVER deleted or modified.**
  - Automatically updates the current active photo, or displays the clean empty state if all photos are deleted.

### 10. Full Page Zoom Option & Minimized Horizontal Profile Bar
- **Full Page Zoom Mode**:
  - Added a **Full Page** toggle button in the zoom toolbar next to `100%`.
  - Also toggled with keyboard shortcut **`F`** and exited via **`Escape`** or clicking the active button.
  - Expands the photo comparison viewer to occupy the full 100vw × 100vh display, collapsing the top header, left library sidebar, and right editing controls for a distraction-free inspecting environment.
  - Automatically recalculates zoom scaling so the full photo fits the enlarged workspace without distortion.
- **Horizontal Scrollable Profile Bar**:
  - Re-architected the developed profile results bar into a single-line horizontal flex carousel (`overflow-x: auto; scroll-behavior: smooth;`).
  - Scrollable from left to right and vice versa smoothly using the mouse wheel or horizontal scrolling.
  - Selecting any profile automatically scrolls its card smoothly into view.
- **Minimized Floating Dock in Full Page Mode**:
  - When in Full Page mode, the profile bar docks at the bottom edge as a subtle, translucent peeking strip (`opacity: 0.25`).
  - Moving the mouse down over the bottom edge smoothly expands the dock (`transform: translateY(0); opacity: 1; backdrop-filter: blur(16px)`), allowing quick switching between developed looks without leaving Full Page mode.
