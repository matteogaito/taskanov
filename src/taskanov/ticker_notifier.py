# bg_worker.py
import logging, threading, time, queue
from .notify_os import notify
log = logging.getLogger("taskanov.bg")

class TickerNotifier:
    def __init__(self, backend, interval=1.0):
        self.backend = backend 
        self.interval = interval
        self._stop = threading.Event()
        self._thr = None
        self._tick = 0

    def start(self):
        if self._thr and self._thr.is_alive(): return
        self._stop.clear()
        self._thr = threading.Thread(target=self._run, daemon=True, name="bg-worker")
        self._thr.start()
        log.info("bg: started")

    def stop(self):
        self._stop.set()
        if self._thr: self._thr.join(timeout=2)
        log.info("bg: stopped")

    def _run(self):
        start = time.perf_counter()
        while not self._stop.wait(self.interval):
            self._tick += 1
            uptime = time.perf_counter() - start
            actives = self.backend.get_active_timer()
            log.debug("actives {} uptime {}".format(actives, uptime))
            if not actives[0]:
                notify(title="taskanov", message="What are you working on? Open Taskanov")
            else:
                notify(title="taskanov", message="Are you still working on {}?".format(actives[1]))

