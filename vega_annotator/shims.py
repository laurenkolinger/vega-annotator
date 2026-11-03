"""Make VEGA's Linux code run unchanged on a Mac or Windows.

VEGA's annotator files stay byte for byte as they are on the mainframe, so what
differs by operating system is supplied around them, before they are imported:

- Windows has no fcntl module, and _reefreview/ledger_io.py imports it outright.
- Windows refuses symbolic links without special rights, and the export links images.
- Windows briefly holds files open (antivirus, the search indexer), which makes
  os.replace fail where Linux never would.
- Windows writes CRLF and the local code page in text mode, and these files travel
  back to a Linux machine, so text mode is pinned to LF, and the annotator process
  runs in Python's UTF-8 mode.
- VEGA's "open folder" helper shells out to xdg-open, which exists only on Linux.
"""

import builtins
import io
import os
import shutil
import subprocess
import sys
import time
import types

LOCK_RETRY_S = 0.05
LOCK_TIMEOUT_S = 30
REPLACE_ATTEMPTS = 20
REPLACE_RETRY_S = 0.1
WINDOWS_PRIVILEGE_ERROR = 1314


def make_fcntl_module(msvcrt_module, sleep=time.sleep, clock=time.monotonic):
    """Build a stand-in for the fcntl module on top of msvcrt.

    Parameters:
        msvcrt_module: the msvcrt module, passed in so tests can fake it.
        sleep: the wait function between lock attempts.
        clock: the monotonic clock used for the timeout.
    Returns:
        module: a module with LOCK_SH, LOCK_EX, LOCK_NB, LOCK_UN, and flock.
    """
    module = types.ModuleType("fcntl")
    module.LOCK_SH, module.LOCK_EX, module.LOCK_NB, module.LOCK_UN = 1, 2, 4, 8

    def flock(fd, operation):
        """Take or release a lock on the first byte of an open file.

        Parameters:
            fd: a file descriptor, or an object with fileno().
            operation: LOCK_EX or LOCK_SH, with LOCK_NB for no waiting, or LOCK_UN.
        Returns:
            None.
        Raises:
            BlockingIOError: when LOCK_NB is set and the lock is held.
            TimeoutError: when a blocking lock stays held past LOCK_TIMEOUT_S.
        """
        fileno = fd.fileno() if hasattr(fd, "fileno") else fd
        os.lseek(fileno, 0, os.SEEK_SET)
        if operation & module.LOCK_UN:
            msvcrt_module.locking(fileno, msvcrt_module.LK_UNLCK, 1)
            return
        deadline = clock() + LOCK_TIMEOUT_S
        while True:
            try:
                msvcrt_module.locking(fileno, msvcrt_module.LK_NBLCK, 1)
                return
            except OSError as exc:
                if operation & module.LOCK_NB:
                    raise BlockingIOError(str(exc)) from exc
                if clock() >= deadline:
                    raise TimeoutError(f"lock held for over {LOCK_TIMEOUT_S} s") from exc
                sleep(LOCK_RETRY_S)

    module.flock = flock
    return module


def symlink_with_fallback(real_symlink, src, dst, *args, **kwargs):
    """Make a link, falling back to a hard link and then a copy.

    Parameters:
        real_symlink: the original os.symlink.
        src: the link target.
        dst: the link path.
        args, kwargs: passed on to os.symlink.
    Returns:
        None.
    Raises:
        OSError: only when every route fails.
    """
    try:
        real_symlink(src, dst, *args, **kwargs)
        return
    except (OSError, NotImplementedError):
        pass
    resolved = src if os.path.isabs(src) else os.path.join(os.path.dirname(dst), src)
    try:
        os.link(resolved, dst)
    except OSError:
        shutil.copy2(resolved, dst)


def replace_with_retry(real_replace, src, dst, sleep=time.sleep, **kwargs):
    """Call os.replace, retrying while another program briefly holds the file.

    Parameters:
        real_replace: the original os.replace.
        src: the source path.
        dst: the destination path.
        sleep: the wait function between attempts.
        kwargs: passed on to os.replace.
    Returns:
        None.
    Raises:
        PermissionError: when the file stays held through every attempt.
    """
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            real_replace(src, dst, **kwargs)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            sleep(REPLACE_RETRY_S)


def lf_open(real_open):
    """Wrap an open function so text-mode writes use LF line endings.

    Parameters:
        real_open: the original open function.
    Returns:
        callable: the wrapped function, with the same signature.
    """
    def wrapped(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None,
                closefd=True, opener=None):
        """Open a file, pinning newline to LF for a text-mode write."""
        writing = any(flag in mode for flag in "wax+")
        if newline is None and writing and "b" not in mode:
            newline = "\n"
        return real_open(file, mode, buffering, encoding, errors, newline, closefd, opener)
    return wrapped


def make_host_open_module(platform=None, popen=subprocess.Popen, startfile=None):
    """Build the stand-in for VEGA's host_open helper.

    Parameters:
        platform: a sys.platform value; defaults to the running system.
        popen: the process starter, passed in so tests can fake it.
        startfile: os.startfile, passed in so tests can fake it.
    Returns:
        module: a module with open_in_file_manager(path) -> (ok, error text).
    """
    platform = platform or sys.platform
    module = types.ModuleType("host_open")

    def open_in_file_manager(path):
        """Show a folder or file in Finder or File Explorer.

        Parameters:
            path: the path to open.
        Returns:
            tuple: (True, "") when the request went out, (False, reason) otherwise.
        """
        try:
            if platform == "win32":
                (startfile or os.startfile)(path)
            elif platform == "darwin":
                popen(["open", path])
            else:
                popen(["xdg-open", path])
            return True, ""
        except (OSError, AttributeError) as exc:
            return False, f"Could not open {path}: {exc}"

    module.open_in_file_manager = open_in_file_manager
    module.gui_env = lambda: dict(os.environ)
    return module


def install(platform=None):
    """Install the stand-ins this operating system needs. Safe to call twice.

    Parameters:
        platform: a sys.platform value; defaults to the running system.
    Returns:
        list: the names of what was installed, for the log.
    """
    platform = platform or sys.platform
    installed = []
    if "host_open" not in sys.modules:
        sys.modules["host_open"] = make_host_open_module(platform)
        installed.append("host_open")
    if platform == "win32" and not getattr(os, "_vega_annotator_shimmed", False):
        import msvcrt
        sys.modules.setdefault("fcntl", make_fcntl_module(msvcrt))
        real_symlink, real_replace = os.symlink, os.replace
        os.symlink = lambda src, dst, *a, **k: symlink_with_fallback(real_symlink, src, dst, *a, **k)
        os.replace = lambda src, dst, **k: replace_with_retry(real_replace, src, dst, **k)
        builtins.open = lf_open(builtins.open)
        io.open = lf_open(io.open)
        os._vega_annotator_shimmed = True
        installed.extend(["fcntl", "symlink", "replace", "lf_open"])
    return installed
