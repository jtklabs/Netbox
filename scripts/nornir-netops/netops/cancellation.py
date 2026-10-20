"""Cooperative, thread-local cancellation for one scheduled device job.

Cancelled bypasses ordinary retry/failure handlers. Only the scheduler catches it,
after device contexts and local locks have unwound. No global worker signal is used.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import os
import signal
import subprocess
import threading
import time

_current = ContextVar('job_cancellation', default=None)


class Cancelled(BaseException):
    pass


class Control:
    def __init__(self):
        self.requested = threading.Event()

    def observe(self, response):
        if response.get('cancel_requested') is True:
            self.requested.set()


@contextmanager
def bind(control):
    token = _current.set(control)
    try:
        yield
    finally:
        _current.reset(token)


def checkpoint():
    control = _current.get()
    if control is not None and control.requested.is_set():
        raise Cancelled()


def sleep(seconds, fallback=time.sleep):
    control = _current.get()
    if control is None:
        return fallback(seconds)
    control.requested.wait(max(0, seconds))
    checkpoint()


def run(command, *, timeout, capture_output=True, text=True, **kwargs):
    """Reap only this job's feature process group before acknowledging a stop."""
    if _current.get() is None:
        return subprocess.run(command, timeout=timeout, capture_output=capture_output, text=text, **kwargs)
    checkpoint()
    deadline = time.monotonic() + timeout
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=text, start_new_session=True, **kwargs) as child:
        try:
            while True:
                checkpoint()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    stdout, stderr = child.communicate(timeout=min(0.5, remaining))
                    checkpoint()
                    return subprocess.CompletedProcess(command, child.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise
        except BaseException:
            # Each invocation owns its new session. Never signal the poller or
            # another job's process group, even when feature children are nested.
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.communicate()
            # A descendant can ignore TERM and close its inherited output pipes.
            # Reap any remaining members even when communicate already returned.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            raise
