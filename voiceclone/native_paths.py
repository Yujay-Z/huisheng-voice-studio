"""Keep Windows native FST loading independent of Unicode absolute paths."""
import importlib
import os
from pathlib import Path


def native_resource_path(path):
    value = os.fspath(path)
    if os.name != 'nt' or value.isascii():
        return value
    relative = os.path.relpath(value, Path.cwd())
    if relative.isascii():
        return relative
    raise RuntimeError('文字资源的相对路径仍含非 ASCII 字符，请检查 wetext 安装位置。')


def preload_wetext():
    # wetext eagerly opens FSTs at import time. Change only the loader during
    # this import, return real native objects, and restore the public class.
    if os.name != 'nt':
        return importlib.import_module('wetext')
    import kaldifst
    original = kaldifst.TextNormalizer
    def load_resource(path, *args, **kwargs):
        return original(native_resource_path(path), *args, **kwargs)
    kaldifst.TextNormalizer = load_resource
    try:
        return importlib.import_module('wetext')
    finally:
        kaldifst.TextNormalizer = original
