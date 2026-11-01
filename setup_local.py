"""Project-local installer. Never edits the registry, PATH, or system Python."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
(ROOT / '.cache/temp').mkdir(parents=True, exist_ok=True)
os.environ.update(UV_CACHE_DIR=str(ROOT / '.cache/uv'),
                  UV_PYTHON_INSTALL_DIR=str(ROOT / '.runtime/python'),
                  UV_TOOL_DIR=str(ROOT / '.tools/uv-tools'),
                  HF_HOME=str(ROOT / '.cache/huggingface'),
                  PIP_CACHE_DIR=str(ROOT / '.cache/pip'),
                  PYTHONUTF8='1', PYTHONIOENCODING='utf-8', UV_LINK_MODE='copy',
                  HF_HUB_DOWNLOAD_TIMEOUT='60',
                  TEMP=str(ROOT / '.cache/temp'), TMP=str(ROOT / '.cache/temp'))
os.environ.pop('PIP_NO_INDEX', None)
os.environ.pop('UV_PROJECT_ENVIRONMENT', None)
os.environ['PIP_CONFIG_FILE'] = os.devnull
if not shutil.which('git'):
    bundled_git = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/native/git/cmd'
    if (bundled_git / 'git.exe').exists():
        os.environ['PATH'] = str(bundled_git) + os.pathsep + os.environ.get('PATH', '')


def run(args, cwd=ROOT):
    print('\n>>> ' + subprocess.list2cmdline(list(map(str, args))), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, env=os.environ, check=True)


def python_in(path):
    return path / 'Scripts/python.exe'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--engine', choices=['app', 'index'], default='app')
    p.add_argument('--skip-models', action='store_true')
    p.add_argument('--model-source', choices=['auto', 'huggingface', 'modelscope'], default='auto')
    args = p.parse_args()
    if shutil.disk_usage(ROOT).free < 8 * 2**30:
        raise RuntimeError('至少需要 8 GB 剩余磁盘空间；首次安装还需为依赖和下载缓存预留空间。')
    (ROOT / '.tools').mkdir(exist_ok=True)
    uv = ROOT / '.tools/uv/uv.exe'
    if not uv.exists():
        run([sys.executable, '-m', 'pip', 'install', '--upgrade', '--index-url', 'https://pypi.org/simple', '--target', ROOT / '.tools/uv-package', 'uv'])
        source = next((ROOT / '.tools/uv-package').rglob('uv.exe'))
        uv.parent.mkdir(exist_ok=True)
        shutil.copy2(source, uv)
    run([uv, 'python', 'install', '3.11'])
    if not python_in(ROOT / '.venv').exists():
        run([uv, 'venv', '--python', '3.11', ROOT / '.venv'])
    py = python_in(ROOT / '.venv')
    run([uv, 'pip', 'install', '--python', py, '-r', 'requirements.txt'])
    run([py, '-c', "import imageio_ffmpeg, pathlib, shutil; d=pathlib.Path('.tools/ffmpeg'); d.mkdir(exist_ok=True); shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(),d/'ffmpeg.exe')"])
    asr_prepared = False
    if args.engine == 'index':
        repo = ROOT / 'engines/index'
        repo.parent.mkdir(exist_ok=True)
        if not repo.exists():
            run(['git', 'clone', '--depth', '1', '--branch', 'v2.5.0',
                 'https://github.com/index-tts/index-tts.git', repo])
        if not (repo / 'indextts/infer_v2_5.py').exists():
            raise RuntimeError('仓库未包含 IndexTTS-2.5 接口，请检查源码版本。')
        run([uv, 'sync', '--no-dev', '--python', '3.11'], cwd=repo)
        model_id = 'IndexTeam/IndexTTS-2.5'
        if not args.skip_models:
            run([python_in(repo / '.venv'), ROOT / 'download_models.py', '--source', args.model_source])
            asr_prepared = True
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
        (repo / 'local-install.json').write_text(json.dumps({'revision': revision, 'model': model_id, 'installed': time.time()}, indent=2), encoding='utf-8')
    if not args.skip_models and not asr_prepared:
        run([py, '-c', "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8', download_root='models/asr')"])
    print('\n安装完成。双击“启动音色工作室.cmd”。', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('\n安装失败：' + str(exc), file=sys.stderr)
        sys.exit(1)
