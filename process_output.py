"""UTF-8 process transport with an independent, durable session log. No Qt."""
import codecs
from collections import deque
from contextlib import contextmanager
import os
import sys
from threading import Lock, get_ident


def _duplicate_stdout():
    if os.name != "nt":
        return os.fdopen(os.dup(1), "wb", buffering=0)
    # A windowed PyInstaller process has sys.__stdout__ == None. QProcess still
    # provides an inherited Windows pipe handle; duplicate it before owning an fd.
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                      ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                      wintypes.BOOL, wintypes.DWORD]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.GetStdHandle(-11 & 0xffffffff)
    if handle in (None, 0, ctypes.c_void_p(-1).value):
        raise OSError("Parent output pipe is unavailable")
    process, duplicate = kernel.GetCurrentProcess(), wintypes.HANDLE()
    if not kernel.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(duplicate.value, os.O_WRONLY | os.O_BINARY)
    except OSError:
        kernel.CloseHandle(duplicate)
        raise
    return os.fdopen(descriptor, "wb", buffering=0)


@contextmanager
def parent_output():
    original = getattr(sys, "__stdout__", None)
    binary = getattr(original, "buffer", None)
    if binary is not None:
        yield binary  # The interpreter owns this stream.
    else:
        with _duplicate_stdout() as stream:
            yield stream


class SessionOutput:
    """Serialize complete Python log lines to the file and the parent pipe."""
    encoding, errors = "utf-8", "replace"

    def __init__(self, archive, pipe=None):
        self.archive, self.pipe = archive, pipe
        self._lock, self._tails = Lock(), {}

    def _publish(self, text):
        self.archive.write(text)
        self.archive.flush()
        if self.pipe is not None:
            try:
                data = memoryview(text.encode("utf-8", errors="replace"))
                while data:
                    written = self.pipe.write(data)
                    if not written:
                        raise OSError("Parent pipe closed")
                    data = data[written:]
                self.pipe.flush()
            except (OSError, ValueError):
                # Closing the UI pipe must not turn logging into a bot failure.
                self.pipe = None

    def write(self, text):
        with self._lock:
            thread = get_ident()
            lines = (self._tails.pop(thread, "") + text).split("\n")
            tail = lines.pop()
            if tail:
                self._tails[thread] = tail
            if lines:
                self._publish("\n".join(lines) + "\n")
        return len(text)

    def flush(self):
        with self._lock:
            tail = self._tails.pop(get_ident(), "")
            if tail:
                self._publish(tail)
            self.archive.flush()

    def finish(self):
        with self._lock:
            for tail in self._tails.values():
                self._publish(tail)
            self._tails.clear()
            self.archive.flush()

    def isatty(self):
        return False


class ProcessLines:
    """Decode arbitrary pipe chunks, retaining incomplete UTF-8 and final lines."""
    def __init__(self):
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.lines, self.tail, self.finished = deque(), "", False

    def feed_text(self, text):
        parts = (self.tail + text).split("\n")
        self.tail = parts.pop()
        self.lines.extend(line.rstrip("\r") for line in parts)

    def feed(self, data, *, final=False):
        if self.finished:
            return
        self.feed_text(self.decoder.decode(data, final=final))
        if final:
            if self.tail:
                self.lines.append(self.tail.rstrip("\r"))
                self.tail = ""
            self.finished = True
