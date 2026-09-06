import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.studio'
PHOTO_ROOT = Path(os.environ.get('PHOTO_ROOT', ROOT)).resolve()
OUTPUT = PHOTO_ROOT / 'PROCCESED' / 'PERFILES'
DARKTABLE = Path(os.environ.get('DARKTABLE_MCP', str(Path.home() / 'AppData/Local/Programs/darktable-mcp-nightly/bin/darktable-mcp.exe')))
MODEL_URL = os.environ.get('MODEL_URL', 'http://127.0.0.1:8081/v1')
RAW_EXTENSIONS = {'.arw', '.cr2', '.cr3', '.nef', '.dng', '.raf', '.rw2', '.orf', '.pef', '.jpg', '.jpeg', '.png', '.tif', '.tiff'}
