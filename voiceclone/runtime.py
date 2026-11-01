"""Resolve development and fully bundled Windows runtimes without machine paths."""
import os
from pathlib import Path


def app_python(root, windowless=False):
    root = Path(root)
    name = 'pythonw.exe' if windowless else 'python.exe'
    portable = root / 'runtime' / 'app' / name
    return portable if portable.is_file() else root / '.venv' / 'Scripts' / name


def engine_python(root):
    root = Path(root)
    portable = root / 'runtime' / 'engine' / 'python.exe'
    return portable if portable.is_file() else root / 'engines/index/.venv/Scripts/python.exe'


def configure_runtime(root):
    root = Path(root)
    if not (root / 'offline-package.json').is_file():
        return
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_DATASETS_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1',
                      HF_HOME=str(root / '.cache/huggingface'),
                      NLTK_DATA=str(root / 'models/nltk_data'),
                      PYTHONNOUSERSITE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
