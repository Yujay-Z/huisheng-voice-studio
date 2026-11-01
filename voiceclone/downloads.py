"""Bounded, visible downloads for the BigVGAN file missing from the upstream mapping."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import re
import time


def file_matches(path, size=None, sha256=None):
    path = Path(path)
    if not path.is_file() or (size is not None and path.stat().st_size != int(size)):
        return False
    if sha256:
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest().lower() == sha256.lower()
    return True


@contextmanager
def download_lock(path, busy_message='已有补下载任务在运行，请使用原窗口，不要重复启动。'):
    """An OS lock releases even if the user closes the command window."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as lock:
        lock.seek(0, 2)
        if not lock.tell():
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except ImportError:
            import fcntl
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError(busy_message) from exc
        except OSError as exc:
            raise RuntimeError(busy_message) from exc
        try:
            yield
        finally:
            lock.seek(0)
            try:
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            except ImportError:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_UN)


def download_file(url, target, size, sha256, *, session=None, attempts=2, chunk_bytes=8 * 1024 * 1024):
    import requests
    from tqdm import tqdm
    target = Path(target)
    if file_matches(target, size, sha256):
        print('已校验，跳过下载：' + target.name, flush=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + '.part')
    own_session = session is None
    session = session or requests.Session()
    try:
        for attempt in range(1, attempts + 1):
            offset = partial.stat().st_size if partial.exists() else 0
            if offset >= size:
                if file_matches(partial, size, sha256):
                    partial.replace(target)
                    return
                partial.replace(partial.with_name(partial.name + '.invalid'))
                offset = 0
            print(f'\n下载 {target.name}，第 {attempt}/{attempts} 次连接，已保存 {offset / 2**20:.1f} MiB；无数据超时 20 秒。', flush=True)
            try:
                started = time.monotonic()
                with tqdm(total=size, initial=offset, unit='B', unit_scale=True, desc=target.name) as progress:
                    while offset < size:
                        end = min(offset + chunk_bytes - 1, size - 1)
                        headers = {'Range': f'bytes={offset}-{end}'}
                        with session.get(url, headers=headers, timeout=(5, 20), stream=True) as response:
                            response.raise_for_status()
                            if 'text/html' in response.headers.get('Content-Type', ''):
                                raise RuntimeError('下载源返回了网页，未取得模型文件。')
                            if response.status_code == 206:
                                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                                if not match or int(match[1]) != offset or int(match[2]) > end or int(match[3]) != size:
                                    raise RuntimeError('下载源返回的分段位置或总大小不正确。')
                                expected_end = int(match[2]) + 1
                            elif response.status_code == 200:
                                if offset:
                                    print('下载源不支持续传，只重新获取当前未完成文件；其他文件保持不变。', flush=True)
                                offset = 0
                                progress.reset(total=size)
                                expected_end = size
                            else:
                                raise RuntimeError('下载源返回了不支持的状态码。')
                            with partial.open('ab' if offset else 'wb') as stream:
                                for chunk in response.iter_content(chunk_size=256 * 1024):
                                    if time.monotonic() - started > 1800:
                                        raise RuntimeError('当前文件下载超过 30 分钟，已停止；片段已保留。')
                                    if chunk:
                                        if offset + len(chunk) > expected_end:
                                            raise RuntimeError('下载内容超过请求的分段大小。')
                                        stream.write(chunk)
                                        stream.flush()
                                        offset += len(chunk)
                                        progress.update(len(chunk))
                            if offset != expected_end:
                                raise RuntimeError('当前分段未完整返回，已保留收到的数据。')
                if not file_matches(partial, size, sha256):
                    raise RuntimeError('当前模型文件未完整下载或 SHA-256 不符。')
                partial.replace(target)
                print('下载并校验通过：' + target.name, flush=True)
                return
            except (requests.RequestException, RuntimeError) as exc:
                print('本次连接未完成：' + str(exc), flush=True)
                if attempt == attempts:
                    raise RuntimeError('下载连接不可用，已保留片段。请把这条错误发送给我，不需要重装依赖。') from exc
    finally:
        if own_session:
            session.close()
