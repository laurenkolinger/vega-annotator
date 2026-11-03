"""Tests for the Windows stand-ins in shims.py, with Windows's own calls faked."""

import errno
import os
import sys
import types

import pytest

from vega_annotator import shims


class FakeMsvcrt:
    """Records locking calls and refuses a lock a set number of times."""

    LK_NBLCK, LK_UNLCK = 2, 0

    def __init__(self, refuse=0):
        self.refuse = refuse
        self.calls = []

    def locking(self, fd, mode, size):
        self.calls.append((mode, size))
        if mode == self.LK_NBLCK and self.refuse > 0:
            self.refuse -= 1
            raise OSError(errno.EACCES, "held")


def test_flock_takes_and_releases(tmp_path):
    fake = FakeMsvcrt()
    module = shims.make_fcntl_module(fake)
    with open(tmp_path / "lock", "a+") as handle:
        module.flock(handle, module.LOCK_EX)
        module.flock(handle.fileno(), module.LOCK_UN)
    assert fake.calls == [(fake.LK_NBLCK, 1), (fake.LK_UNLCK, 1)]


def test_flock_nonblocking_raises_when_held(tmp_path):
    module = shims.make_fcntl_module(FakeMsvcrt(refuse=1))
    with open(tmp_path / "lock", "a+") as handle:
        with pytest.raises(BlockingIOError):
            module.flock(handle, module.LOCK_EX | module.LOCK_NB)


def test_flock_blocking_retries_until_free(tmp_path):
    waits = []
    module = shims.make_fcntl_module(FakeMsvcrt(refuse=3), sleep=waits.append)
    with open(tmp_path / "lock", "a+") as handle:
        module.flock(handle, module.LOCK_EX)
    assert len(waits) == 3


def test_flock_blocking_gives_up_after_the_timeout(tmp_path):
    ticks = iter(range(0, 1000, 10))
    module = shims.make_fcntl_module(FakeMsvcrt(refuse=10 ** 6), sleep=lambda _s: None, clock=lambda: next(ticks))
    with open(tmp_path / "lock", "a+") as handle:
        with pytest.raises(TimeoutError):
            module.flock(handle, module.LOCK_EX)


def test_symlink_falls_back_to_hard_link(tmp_path):
    target = tmp_path / "target.jpg"
    target.write_bytes(b"data")

    def refuse(*_args, **_kwargs):
        raise OSError(1314, "A required privilege is not held by the client")

    shims.symlink_with_fallback(refuse, str(target), str(tmp_path / "link.jpg"))
    assert (tmp_path / "link.jpg").read_bytes() == b"data"
    assert not (tmp_path / "link.jpg").is_symlink()


def test_symlink_relative_target_resolves_against_the_link(tmp_path):
    (tmp_path / "raw").mkdir()
    (tmp_path / "images").mkdir()
    (tmp_path / "raw" / "a.jpg").write_bytes(b"data")

    def refuse(*_args, **_kwargs):
        raise NotImplementedError

    shims.symlink_with_fallback(refuse, "../raw/a.jpg", str(tmp_path / "images" / "a.jpg"))
    assert (tmp_path / "images" / "a.jpg").read_bytes() == b"data"


def test_symlink_uses_the_real_call_when_it_works(tmp_path):
    made = []
    shims.symlink_with_fallback(lambda src, dst: made.append((src, dst)), "a", "b")
    assert made == [("a", "b")]


def test_replace_retries_then_succeeds():
    attempts = []

    def flaky(src, dst):
        attempts.append((src, dst))
        if len(attempts) < 3:
            raise PermissionError("held by the virus scanner")

    shims.replace_with_retry(flaky, "a", "b", sleep=lambda _s: None)
    assert len(attempts) == 3


def test_replace_gives_up_loudly():
    def always(_src, _dst):
        raise PermissionError("held")

    with pytest.raises(PermissionError):
        shims.replace_with_retry(always, "a", "b", sleep=lambda _s: None)


@pytest.mark.parametrize("mode,given,expected", [
    ("w", None, "\n"), ("a", None, "\n"), ("x", None, "\n"), ("r+", None, "\n"),
    ("r", None, None), ("wb", None, None), ("w", "", ""), ("w", "\r\n", "\r\n")])
def test_lf_open_pins_only_text_writes(mode, given, expected):
    seen = {}

    def fake_open(file, mode, buffering, encoding, errors, newline, closefd, opener):
        seen["newline"] = newline

    shims.lf_open(fake_open)("x", mode, newline=given)
    assert seen["newline"] == expected


def test_host_open_routes_by_platform():
    started = []
    mac = shims.make_host_open_module("darwin", popen=started.append)
    assert mac.open_in_file_manager("/x") == (True, "")
    assert started == [["open", "/x"]]
    opened = []
    windows = shims.make_host_open_module("win32", startfile=opened.append)
    assert windows.open_in_file_manager("C:/x") == (True, "")
    assert opened == ["C:/x"]


def test_host_open_reports_failure():
    def boom(_command):
        raise OSError("no such program")

    ok, reason = shims.make_host_open_module("linux", popen=boom).open_in_file_manager("/x")
    assert ok is False and "/x" in reason


def test_install_on_mac_leaves_os_functions_alone(monkeypatch):
    monkeypatch.delitem(sys.modules, "host_open", raising=False)
    real_replace, real_symlink = os.replace, os.symlink
    installed = shims.install("darwin")
    assert installed == ["host_open"]
    assert os.replace is real_replace and os.symlink is real_symlink
    assert isinstance(sys.modules["host_open"], types.ModuleType)
    assert shims.install("darwin") == []
