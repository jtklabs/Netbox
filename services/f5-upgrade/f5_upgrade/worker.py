import os
import signal
import socket
import threading
import uuid

from .config import Settings
from .engine import Runner
from .store import Store


def process(settings, store, job, worker):
    done = threading.Event()

    def heartbeat():
        while not done.wait(10):
            if not store.heartbeat(job["id"], worker):
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        Runner(settings, store, job, worker).run()
    finally:
        done.set()
        thread.join(timeout=15)


def main():
    os.umask(0o077)
    settings = Settings.load()
    store = Store(settings.data_dir / "jobs.sqlite3")
    worker = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    stop = threading.Event()
    # SIGTERM drains the current job before exiting; SIGKILL leaves a fenced recovery job.
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        store.reap(settings.stale_seconds)
        job = store.claim(worker)
        if job:
            process(settings, store, job, worker)
        else:
            stop.wait(2)


if __name__ == "__main__":
    main()
