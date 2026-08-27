"""The debug log: everything the terminal deliberately does not show.

The file is opened lazily, so a clean run leaves no file behind. A run with
failures writes one entry per device -- the summary the terminal showed, plus
the full traceback -- and the terminal prints the path once at the end.
"""

from __future__ import annotations

import logging
import traceback
from typing import List, Optional, Tuple

LOGGER_NAME = "netops"
DEFAULT_LOG_FILE = "netops-debug.log"

#: Chatty enough to diagnose a timeout, far too chatty for a terminal.
TRANSCRIPT_LOGGERS = ("netmiko", "paramiko")

#: nornir logs a full traceback per failed task at ERROR. With no handler
#: configured those records reach logging's lastResort handler, which writes
#: them to stderr -- one traceback per device, which is exactly the wall of
#: text this module exists to prevent. So we take the logger over.
CAPTURED_LOGGERS = ("nornir",)


class DebugLog:
    """Wraps the logger so the caller can ask whether anything was written."""

    def __init__(
        self, logger: Optional[logging.Logger], path: Optional[str], debug: bool = False
    ) -> None:
        self.logger = logger
        self.path = path
        self.debug = debug
        self.entries = 0
        #: Run context, held back until there is a failure to attach it to, so
        #: a clean run leaves no file behind.
        self._pending: List[Tuple[str, tuple]] = []

    @property
    def used(self) -> bool:
        return self.entries > 0

    def failure(self, host: str, summary: str, exc: Optional[BaseException]) -> None:
        if self.logger is None:
            return
        self._flush()
        detail = ""
        if exc is not None:
            detail = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ).rstrip()
        self.logger.error("%s -- %s\n%s", host, summary, detail)
        self.entries += 1

    def note(self, message: str, *args) -> None:
        """Run context, so an entry can be tied back to what was running."""
        if self.logger is None:
            return
        if self.debug:
            self.logger.info(message, *args)  # asked for everything
        else:
            self._pending.append((message, args))

    def _flush(self) -> None:
        for message, args in self._pending:
            self.logger.info(message, *args)
        self._pending.clear()


def _take_over(name: str, handler: Optional[logging.Handler], level: int) -> None:
    """Route a library's logging into our file -- or nowhere -- but never to
    stderr. Without `propagate = False` the records climb to the root logger,
    which has no handler and therefore falls back to printing them."""
    logger = logging.getLogger(name)
    logger.propagate = False
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
    logger.addHandler(handler if handler is not None else logging.NullHandler())
    logger.setLevel(level)


def configure(path: Optional[str], debug: bool = False) -> DebugLog:
    """Point every logger that could reach the terminal at `path` instead.

    `path=None` (--no-log-file) still silences them: the terminal report is the
    output, and a traceback is not part of it.
    """
    handler: Optional[logging.Handler] = None
    if path:
        # delay=True: the file is created on the first write, so a clean run
        # does not litter the working directory.
        handler = logging.FileHandler(path, mode="a", delay=True, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
        )

    _take_over(*_capture_spec(CAPTURED_LOGGERS[0], handler, debug, quiet_level=logging.ERROR))
    for name in TRANSCRIPT_LOGGERS:
        # The SSH transcript, for when the summary is not enough.
        _take_over(*_capture_spec(name, handler, debug, quiet_level=logging.WARNING))

    if handler is None:
        return DebugLog(None, None, debug)

    _take_over(LOGGER_NAME, handler, logging.DEBUG)
    return DebugLog(logging.getLogger(LOGGER_NAME), path, debug)


def _capture_spec(name, handler, debug, quiet_level):
    return name, handler, logging.DEBUG if debug else quiet_level
