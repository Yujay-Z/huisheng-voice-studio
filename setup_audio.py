"""Install optional CPU audio processing and two official, project-local models."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
MODELS = [
    ('gtcrn_simple.onnx', 'speech-enhancement-models', 535638, None),
    ('UVR_MDXNET_9482.onnx', 'source-separation-models', 29704738,
     '9d78f8566fa8198065214ab628be1de966a500c57786695aa4b13e2b27a7727d'),
]


def download(name, release, size, digest):
    directory = ROOT / 'models/audio'
    directory.mkdir(parents=True, exist_ok=True)
    target, partial = directory / name, directory / (name + '.part')
    if target.exists() and target.stat().st_size == size and (not digest or hashlib.sha256(target.read_bytes()).hexdigest() == digest):
        print('已就绪，跳过：' + name, flush=True)
        return
    url = f'https://github.com/k2-fsa/sherpa-onnx/releases/download/{release}/{name}'
    for attempt in range(3):
        try:
            offset = partial.stat().st_size if partial.exists() else 0
            headers = {'User-Agent': 'VoiceStudio-AudioSetup'}
            if offset:
                headers['Range'] = f'bytes={offset}-'
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=20) as response:
                append = response.status == 206 and response.headers.get('Content-Range', '').startswith(f'bytes {offset}-')
                if not append:
                    offset = 0
                last = 0
                with partial.open('ab' if append else 'wb') as output:
                    while chunk := response.read(512 * 1024):
                        output.write(chunk)
                        offset += len(chunk)
                        if time.monotonic() - last > 1:
                            print(f'{name}: {min(100, offset / size * 100):.0f}% ({offset / 2**20:.1f}/{size / 2**20:.1f} MB)', flush=True)
                            last = time.monotonic()
            if partial.stat().st_size != size or (digest and hashlib.sha256(partial.read_bytes()).hexdigest() != digest):
                partial.unlink(missing_ok=True)
                raise RuntimeError('模型大小或 SHA-256 不匹配')
            partial.replace(target)
            print('下载完成：' + name, flush=True)
            return
        except Exception as exc:
            print(f'第 {attempt + 1} 次未完成，保留缓存：{exc}', flush=True)
            if attempt == 2:
                raise
    raise RuntimeError('模型下载未完成')


def main():
    os.chdir(ROOT)
    os.environ.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', UV_CACHE_DIR=str(ROOT / '.cache/uv'))
    try:
        installed = importlib.metadata.version('sherpa-onnx')
    except importlib.metadata.PackageNotFoundError:
        installed = None
    if installed != '2.0.1':
        subprocess.run([str(ROOT / '.tools/uv/uv.exe'), 'pip', 'install', '--python', sys.executable,
                        '--index-url', 'https://pypi.org/simple', 'sherpa-onnx==2.0.1'], check=True)
    for item in MODELS:
        download(*item)
    import sherpa_onnx as sherpa
    denoise = sherpa.OfflineSpeechDenoiser(sherpa.OfflineSpeechDenoiserConfig(model=sherpa.OfflineSpeechDenoiserModelConfig(
        gtcrn=sherpa.OfflineSpeechDenoiserGtcrnModelConfig(model='models/audio/gtcrn_simple.onnx'), num_threads=1, provider='cpu')))
    separate = sherpa.OfflineSourceSeparation(sherpa.OfflineSourceSeparationConfig(model=sherpa.OfflineSourceSeparationModelConfig(
        uvr=sherpa.OfflineSourceSeparationUvrModelConfig(model='models/audio/UVR_MDXNET_9482.onnx'), num_threads=1, provider='cpu')))
    manifest = {'package': 'sherpa-onnx==2.0.1', 'models': {name: hashlib.sha256((ROOT / 'models/audio' / name).read_bytes()).hexdigest() for name, *_ in MODELS}}
    (ROOT / 'models/audio/installed.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('音频处理安装完成，两个模型已通过本地加载检查。重新启动工作室即可使用。', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('安装未完成：' + str(exc), file=sys.stderr)
        raise SystemExit(1)
