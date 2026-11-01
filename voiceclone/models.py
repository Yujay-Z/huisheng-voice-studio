"""Local model checks shared by the downloader and the UI."""
from pathlib import Path

INDEX_FILES = {
    'config.yaml': 100,
    'gpt.pth': 100_000_000,
    'codec.pth': 10_000_000,
    's2mel.pth': 10_000_000,
    'feat1.pt': 1000,
    'feat2.pt': 1000,
    'wav2vec2bert_stats.pt': 1000,
    'multilingual_zh_ja_yue_char_del.tiktoken': 1000,
}
ASR_FILES = {'model.bin': 10_000_000, 'config.json': 100,
             'tokenizer.json': 1000, 'vocabulary.txt': 1000}
INDEX_AUX_FILES = {
    'hf_cache/w2v-bert-2.0/config.json': 100,
    'hf_cache/w2v-bert-2.0/preprocessor_config.json': 100,
    'hf_cache/campplus_cn_common.bin': 1_000_000,
    'hf_cache/bigvgan/config.json': 100,
    'hf_cache/bigvgan/bigvgan_generator.pt': 449_228_171,
}


def missing_files(directory, required):
    directory = Path(directory)
    missing = []
    for name, minimum in required.items():
        file = directory / name
        if not file.is_file() or file.stat().st_size < minimum:
            missing.append(name)
            continue
        with file.open('rb') as stream:
            if stream.read(128).startswith(b'version https://git-lfs.github.com/spec/'):
                missing.append(name)
    return missing


def require_files(directory, required):
    missing = missing_files(directory, required)
    if missing:
        raise RuntimeError('模型文件缺失或不完整：' + ', '.join(missing))


def index_missing_files(directory):
    directory = Path(directory)
    missing = missing_files(directory, {**INDEX_FILES, **INDEX_AUX_FILES})
    bert = directory / 'hf_cache/w2v-bert-2.0'
    if all(missing_files(bert, {name: 10_000_000})
           for name in ['model.safetensors', 'pytorch_model.bin']):
        missing.append('hf_cache/w2v-bert-2.0/model.safetensors（或 pytorch_model.bin）')
    return missing


def require_index_files(directory):
    missing = index_missing_files(directory)
    if missing:
        repair = '补下载BigVGAN.cmd' if all('/bigvgan/' in name for name in missing) else '补下载IndexTTS模型.cmd'
        raise RuntimeError('模型文件缺失或不完整：' + ', '.join(missing) + '。请先运行“' + repair + '”。')


def find_local_asr(root, size='small'):
    base = Path(root) / 'models/asr'
    direct = base / size
    if not missing_files(direct, ASR_FILES):
        return direct
    cache = base / f'models--Systran--faster-whisper-{size}' / 'snapshots'
    if cache.is_dir():
        for candidate in cache.iterdir():
            if candidate.is_dir() and not missing_files(candidate, ASR_FILES):
                return candidate
    return None
