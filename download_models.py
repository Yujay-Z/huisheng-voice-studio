"""Resume IndexTTS downloads without reinstalling environments or packages."""
import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from voiceclone.models import ASR_FILES, INDEX_FILES, missing_files, require_files
from voiceclone.downloads import download_file, download_lock, file_matches

ROOT = Path(__file__).resolve().parent


class TeeOutput:
    def __init__(self, console, log):
        self.console, self.log = console, log
    def write(self, text):
        self.console.write(text)
        self.log.write(text)
        self.log.flush()
        return len(text)
    def flush(self):
        self.console.flush()
        self.log.flush()
    def __getattr__(self, name):
        return getattr(self.console, name)


BIGVGAN_REPO = 'nv-community/bigvgan_v2_22khz_80band_256x'
# NVIDIA's published upstream SHA-256; ModelScope's hosted copy has the same hash.
BIGVGAN_FILES = {
    'bigvgan_generator.pt': (449228171, 'e95ba25972d3de0628d99cd156e9315a9c018899bf739988959ebe3544080ced'),
    'config.json': (1405, '88a1f47acf747db0b21e97a389d838566147f7a5464583ff5c8d819d870f03ee'),
}


def remote_files(source, repo):
    # Obtain remote metadata before calling snapshot_download: some versions return
    # an incomplete local directory when the network is unavailable.
    if source == 'modelscope':
        from modelscope.hub.api import HubApi
        files = HubApi().get_model_files(repo, recursive=True)
        return [{'path': f['Path'], 'size': f.get('Size'), 'sha256': f.get('Sha256')}
                for f in files if f.get('Type') != 'tree']
    from huggingface_hub import HfApi
    info = HfApi().model_info(repo, files_metadata=True, timeout=20)
    result = []
    for file in info.siblings:
        lfs = file.lfs
        digest = (lfs.get('sha256') if isinstance(lfs, dict) else getattr(lfs, 'sha256', None)) if lfs else None
        result.append({'path': file.rfilename, 'size': file.size, 'sha256': digest})
    return result


def verify_remote(directory, metadata, patterns):
    directory = Path(directory).resolve()
    selected = [item for item in metadata if any(fnmatch.fnmatch(item['path'], p) for p in patterns)]
    if not selected:
        raise RuntimeError('下载源未返回所需模型文件，不能认定下载完成。')
    for item in selected:
        path = (directory / item['path']).resolve()
        if not path.is_relative_to(directory):
            raise RuntimeError('下载源包含无效文件路径。')
        if not path.is_file():
            raise RuntimeError('下载未完成，缺少：' + item['path'])
        if item['size'] is not None and path.stat().st_size != int(item['size']):
            raise RuntimeError('文件大小不符，请重试下载：' + item['path'])
        if item['sha256']:
            print('校验 SHA-256：' + item['path'], flush=True)
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest.lower() != item['sha256'].lower():
                raise RuntimeError('文件校验失败，请重试下载：' + item['path'])


def snapshot(source, repo, directory, required):
    print(f'\n下载源：{source} / {repo}\n保存到：{directory}', flush=True)
    patterns = list(required) + ['LICENSE*', 'README*', 'vocabulary.*', 'preprocessor_config.json']
    metadata = remote_files(source, repo)
    available = {item['path'] for item in metadata}
    absent = set(required) - available
    if absent:
        raise RuntimeError('下载源中缺少必要文件：' + ', '.join(sorted(absent)))
    needed = []
    for item in metadata:
        if any(fnmatch.fnmatch(item['path'], pattern) for pattern in patterns):
            candidate = (Path(directory) / item['path']).resolve()
            if not candidate.is_relative_to(Path(directory).resolve()):
                raise RuntimeError('下载源包含无效文件路径。')
            print('检查本地文件：' + item['path'], flush=True)
            if not file_matches(candidate, item.get('size'), item.get('sha256')):
                needed.append(item['path'])
    if not needed:
        require_files(directory, required)
        print('已有文件校验通过，跳过该模型下载。', flush=True)
        return
    print('仅下载缺失或未通过校验的文件：' + ', '.join(needed), flush=True)
    if source == 'modelscope':
        from modelscope.hub.snapshot_download import snapshot_download
        snapshot_download(model_id=repo, local_dir=str(directory), allow_patterns=needed, max_workers=3)
    else:
        from huggingface_hub import snapshot_download
        snapshot_download(repo_id=repo, local_dir=str(directory), allow_patterns=needed, max_workers=3)
    verify_remote(directory, metadata, patterns)
    require_files(directory, required)


def prepare_bigvgan(directory):
    print('\nBigVGAN 使用已核实的 ModelScope nv-community 来源。', flush=True)
    from urllib.parse import urlencode
    for filename, (size, digest) in BIGVGAN_FILES.items():
        url = 'https://modelscope.cn/api/v1/models/' + BIGVGAN_REPO + '/repo?' + urlencode({'Revision': 'master', 'FilePath': filename})
        download_file(url, directory / filename, size, digest)


def download(source):
    repo = ROOT / 'engines/index'
    checkpoints = repo / 'checkpoints'
    asr_dir = ROOT / 'models/asr/small'
    # Main and ASR downloads are retriable through the SDK caches; no local
    # nonempty directory is accepted as proof of success.
    snapshot(source, 'IndexTeam/IndexTTS-2.5', checkpoints, INDEX_FILES)
    sys.path.insert(0, str(repo))
    os.environ['USE_MODELSCOPE'] = 'true' if source == 'modelscope' else 'false'
    from indextts.utils import model_download
    model_download._USING_MODELSCOPE = source == 'modelscope'
    print('\n准备 IndexTTS 辅助模型。已完成的文件会校验并复用。', flush=True)
    # Upstream skips a nonempty w2v directory. A failed prior download may have
    # left only metadata there, so repair it explicitly before its existence check.
    bert = checkpoints / 'hf_cache/w2v-bert-2.0'
    bert_configs = {'config.json': 100, 'preprocessor_config.json': 100}
    bert_weights = any((bert / file).is_file() and (bert / file).stat().st_size > 10_000_000
                       for file in ['model.safetensors', 'pytorch_model.bin'])
    if missing_files(bert, bert_configs) or not bert_weights:
        model_download.snapshot_download('facebook/w2v-bert-2.0', local_dir=str(bert))
    # Download it before upstream's presence check, avoiding the 404 -> stalled
    # hf-mirror route without changing the third-party checkout.
    prepare_bigvgan(checkpoints / 'hf_cache/bigvgan')
    model_download.ensure_models_available(str(checkpoints))
    auxiliary = {
        'hf_cache/w2v-bert-2.0/config.json': 100,
        'hf_cache/w2v-bert-2.0/preprocessor_config.json': 100,
        'hf_cache/campplus_cn_common.bin': 1_000_000,
        'hf_cache/semantic_codec_model.safetensors': 1_000_000,
        'hf_cache/bigvgan/config.json': 100,
        'hf_cache/bigvgan/bigvgan_generator.pt': 1_000_000,
    }
    require_files(checkpoints, auxiliary)
    if not any((bert / file).is_file() and (bert / file).stat().st_size > 10_000_000
               for file in ['model.safetensors', 'pytorch_model.bin']):
        raise RuntimeError('w2v-bert 权重缺失或不完整。')
    try:
        snapshot(source, 'Systran/faster-whisper-small', asr_dir, ASR_FILES)
    except Exception:
        if source != 'modelscope':
            raise
        print('\nModelScope 的 Systran 仓库下载未完成，尝试 pengzhendong 托管副本。', flush=True)
        snapshot(source, 'pengzhendong/faster-whisper-small', asr_dir, ASR_FILES)
    print('\n验证识别模型能够从本地加载（不联网、不生成语音）…', flush=True)
    app_python = ROOT / '.venv/Scripts/python.exe'
    subprocess.run([str(app_python), '-c',
                    "from faster_whisper import WhisperModel; import sys; WhisperModel(sys.argv[1], device='cpu', compute_type='int8', local_files_only=True)",
                    str(asr_dir)], cwd=ROOT, check=True)
    (checkpoints / 'download-verified.json').write_text(
        json.dumps({'source': source, 'verified_at': time.time(), 'asr': str(asr_dir)}, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', choices=['auto', 'huggingface', 'modelscope'], default='modelscope')
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--bigvgan-only', action='store_true')
    args = parser.parse_args()
    os.chdir(ROOT)
    os.environ.update(HF_HOME=str(ROOT / '.cache/huggingface'),
                      MODELSCOPE_CACHE=str(ROOT / '.cache/modelscope'),
                      HF_HUB_DOWNLOAD_TIMEOUT='60', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    if args.bigvgan_only:
        prepare_bigvgan(ROOT / 'engines/index/checkpoints/hf_cache/bigvgan')
        print('\nBigVGAN 补下载完成并校验通过。请重新启动音色工作室。', flush=True)
        return
    if args.check_only:
        main_missing = missing_files(ROOT / 'engines/index/checkpoints', INDEX_FILES)
        asr_missing = missing_files(ROOT / 'models/asr/small', ASR_FILES)
        print('IndexTTS 缺少：' + (', '.join(main_missing) or '无'))
        print('识别模型缺少：' + (', '.join(asr_missing) or '无'))
        return
    for file in [ROOT / 'engines/index/.venv/Scripts/python.exe', ROOT / '.venv/Scripts/python.exe']:
        if not file.exists():
            raise RuntimeError('Python 环境尚未建立，请先运行安装IndexTTS.cmd。')
    if args.source == 'auto':
        try:
            download('huggingface')
        except Exception as exc:
            print('\nHugging Face 下载未完成：' + str(exc) + '\n切换到 ModelScope。', flush=True)
            download('modelscope')
    else:
        download(args.source)
    print('\n模型补下载完成，识别模型本地加载通过。\n请重新启动音色工作室，用短句做首次合成实测。', flush=True)


if __name__ == '__main__':
    (ROOT / 'logs').mkdir(exist_ok=True)
    exit_code = 0
    with (ROOT / 'logs/download-models.log').open('a', encoding='utf-8') as log:
        console_out, console_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = TeeOutput(console_out, log), TeeOutput(console_err, log)
        try:
            with download_lock(ROOT / '.cache/model-download.lock'):
                main()
        except Exception as exc:
            print('\n补下载未完成：' + str(exc) + '\n已保留下载缓存。请保存窗口末尾的错误信息。', file=sys.stderr)
            exit_code = 1
        finally:
            sys.stdout, sys.stderr = console_out, console_err
    sys.exit(exit_code)
