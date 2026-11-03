"""终端「程序正在运行」提示：为示例脚本提供轻量转圈动画。

只用标准库实现，不引入新依赖。当输出流不是终端（重定向到文件、CI、
pytest 的 capsys/capfd）时自动静默，因此既不会污染日志，也不会打断
被断言的 stdout 输出。

典型用法：

    from examples.progress import set_enabled, stage

    with stage('校验并读取样本数据'):
        dataset = load_sample_dataset()

    with stage('诊断组合回测'):
        result = runner.run()
"""

from __future__ import annotations

import os
import sys
import threading
import time
from contextlib import contextmanager
from typing import Generator, TextIO


_REFRESH_INTERVAL = 0.08
_FRAMES = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
_FRAMES_ASCII = '|/-\\'
_OK_MARK = '✓'
_FAIL_MARK = '✗'
_OK_MARK_ASCII = 'OK'
_FAIL_MARK_ASCII = 'FAIL'

_RESET = '\033[0m'
_CYAN = '\033[36m'
_GREEN = '\033[32m'
_RED = '\033[31m'
_DIM = '\033[2m'
_CLEAR_LINE = '\r\033[K'

# None 表示按输出流能力自动判断；True / False 由 set_enabled 强制指定。
_enabled: bool | None = None
# 环境变量 ``EARLY_BIRD_PROGRESS`` 可覆盖自动判断（1/0），便于在非终端环境里试跑。
_ENV_VAR = 'EARLY_BIRD_PROGRESS'


def set_enabled(enabled: bool | None = None) -> None:
    """强制开启或关闭转圈提示；传入 None 恢复自动判断。"""
    global _enabled
    _enabled = enabled


def is_enabled(stream: TextIO | None = None) -> bool:
    """判断当前输出流是否会渲染转圈提示。"""
    if _enabled is not None:
        return _enabled
    override = os.environ.get(_ENV_VAR, '').strip().lower()
    if override in {'1', 'true', 'yes', 'on'}:
        return True
    if override in {'0', 'false', 'no', 'off'}:
        return False
    target = stream if stream is not None else sys.stderr
    try:
        return bool(target.isatty())
    except (AttributeError, ValueError):
        return False


def _supports_unicode(stream: TextIO) -> bool:
    encoding = getattr(stream, 'encoding', None) or 'ascii'
    try:
        _FRAMES.encode(encoding)
        _OK_MARK.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def _write(stream: TextIO, text: str) -> None:
    stream.write(text)
    stream.flush()


class _StageHandle:
    """阶段句柄：``update`` 可在阶段进行中刷新提示文案。"""

    def update(self, message: str) -> None:
        """更新提示文案；静默模式下什么都不做。"""


_SILENT = _StageHandle()


class _Spinner(_StageHandle):
    """后台线程刷新单行转圈动画，结束后清掉残影。"""

    def __init__(self, stream: TextIO, message: str) -> None:
        self._stream = stream
        self._message = message
        self._frames = _FRAMES if _supports_unicode(stream) else _FRAMES_ASCII
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._start = time.perf_counter()
        self._thread = threading.Thread(
            target=self._spin, name='progress-spinner', daemon=True)

    def start(self) -> None:
        self._thread.start()

    def update(self, message: str) -> None:
        with self._lock:
            self._message = message

    def _render(self, frame: str) -> None:
        with self._lock:
            message = self._message
        elapsed = time.perf_counter() - self._start
        line = f'{_CYAN}{frame}{_RESET} {message} {_DIM}{elapsed:4.1f}s{_RESET}'
        _write(self._stream, f'\r{line}')

    def _spin(self) -> None:
        index = 0
        while not self._stopped.is_set():
            self._render(self._frames[index % len(self._frames)])
            index += 1
            self._stopped.wait(_REFRESH_INTERVAL)

    def stop(self) -> None:
        """停止动画并清除当前行，避免残影污染后续输出。"""
        self._stopped.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        _write(self._stream, _CLEAR_LINE)


@contextmanager
def stage(
        message: str,
        stream: TextIO | None = None) -> Generator[_StageHandle, None, None]:
    """在块执行期间显示转圈提示，结束时打印耗时与结果。

    非终端场景下退化为「什么都不做」，块内异常照常向上抛出。
    返回的句柄支持 ``update(message)``，可在同一阶段内更新提示文案。
    """
    target = stream if stream is not None else sys.stderr
    if not is_enabled(target):
        yield _SILENT
        return

    spinner = _Spinner(target, message)
    spinner.start()
    start = time.perf_counter()
    try:
        yield spinner
    except BaseException:
        spinner.stop()
        mark = _FAIL_MARK if _supports_unicode(target) else _FAIL_MARK_ASCII
        elapsed = time.perf_counter() - start
        _write(
            target,
            f'{_RED}{mark}{_RESET} {message} {_DIM}({elapsed:.2f}s){_RESET}\n')
        raise
    else:
        spinner.stop()
        mark = _OK_MARK if _supports_unicode(target) else _OK_MARK_ASCII
        elapsed = time.perf_counter() - start
        _write(
            target,
            f'{_GREEN}{mark}{_RESET} {message} {_DIM}({elapsed:.2f}s){_RESET}\n')
