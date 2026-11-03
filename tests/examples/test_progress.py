import io
import time

import pytest

from examples.progress import is_enabled, set_enabled, stage


class _FakeTTY(io.StringIO):
    """模拟终端：pytest 的 capsys 流不是 tty，这里显式声明成 tty。"""

    encoding = 'utf-8'

    def isatty(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _reset_enabled():
    set_enabled(None)
    yield
    set_enabled(None)


def test_stage_is_silent_when_stream_is_not_a_terminal():
    stream = io.StringIO()

    with stage('计算因子', stream=stream):
        pass

    assert stream.getvalue() == ''
    assert not is_enabled(stream)


def test_stage_renders_spinner_and_elapsed_on_terminal():
    stream = _FakeTTY()

    with stage('计算因子', stream=stream):
        pass

    rendered = stream.getvalue()
    assert '计算因子' in rendered
    assert '✓' in rendered
    assert rendered.endswith('\n')
    # 转圈帧用回车重绘，结束时用 \r\033[K 清掉当前行。
    assert rendered.count('\r') >= 2


def test_stage_marks_failure_and_reraises():
    stream = _FakeTTY()

    with pytest.raises(RuntimeError):
        with stage('组合回测', stream=stream):
            raise RuntimeError('boom')

    rendered = stream.getvalue()
    assert '组合回测' in rendered
    assert '✗' in rendered


def test_set_enabled_forces_off_even_on_terminal():
    stream = _FakeTTY()
    set_enabled(False)

    with stage('计算因子', stream=stream):
        pass

    assert stream.getvalue() == ''


def test_stage_handle_can_update_message_midway():
    stream = _FakeTTY()

    with stage('回测：准备', stream=stream) as progress:
        progress.update('回测：第 3 年')
        time.sleep(0.2)

    assert '回测：第 3 年' in stream.getvalue()


def test_ascii_stream_falls_back_to_ascii_frames():
    class _AsciiTTY(_FakeTTY):
        encoding = 'ascii'

    stream = _AsciiTTY()

    with stage('计算因子', stream=stream):
        pass

    rendered = stream.getvalue()
    assert 'OK' in rendered
    assert '⠋' not in rendered
