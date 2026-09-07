import os
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.studio'
CONFIG_FILE = STATE / 'config.json'

def load_config():
    if CONFIG_FILE.is_file():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {}

def save_config(updates: dict):
    cfg = load_config()
    cfg.update(updates)
    STATE.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding='utf-8')
    return cfg

DEFAULT_PHOTO_ROOT = ROOT.parent / 'FOTOS JAPON' if (ROOT.parent / 'FOTOS JAPON').is_dir() else ROOT
_saved_root = load_config().get('default_photo_root')
if _saved_root and Path(_saved_root).is_dir():
    DEFAULT_PHOTO_ROOT = Path(_saved_root).resolve()

PHOTO_ROOT = Path(os.environ.get('PHOTO_ROOT', DEFAULT_PHOTO_ROOT)).resolve()
OUTPUT = PHOTO_ROOT / 'PROCCESED' / 'PERFILES'
DARKTABLE = Path(os.environ.get('DARKTABLE_MCP', str(Path.home() / 'AppData/Local/Programs/darktable-mcp-nightly/bin/darktable-mcp.exe')))
MODEL_URL = os.environ.get('MODEL_URL', 'http://127.0.0.1:8081/v1')
CAMERA_RAW_EXTENSIONS = {'.arw', '.cr2', '.cr3', '.nef', '.nrw', '.dng', '.raf', '.rw2', '.orf', '.pef', '.srw', '.srf', '.raw'}
RAW_EXTENSIONS = CAMERA_RAW_EXTENSIONS | {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp'}

MODEL_DIR = STATE / 'models' / 'qwen3-vl-4b'
MODEL_FILE = MODEL_DIR / 'Qwen3VL-4B-Instruct-Q4_K_M.gguf'
MMPROJ_FILE = MODEL_DIR / 'mmproj-Qwen3VL-4B-Instruct-F16.gguf'
