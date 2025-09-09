from __future__ import annotations
import logging
import argparse
from pathlib import Path
import queue
from .tui import run_tui
from .config import load_config, state_dir
from .backends import make_backend
from taskanov.logging_setup import setup_logging
#from .notifier import WorkCheckNotifier
from .ticker_notifier import TickerNotifier

def main(argv=None):
    import argparse
    from pathlib import Path
    from .tui import run_tui

    log = setup_logging()

    p = argparse.ArgumentParser(prog="taskanov", description="TUI")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tui", help="Open TUI")
    args = p.parse_args(argv)

    if args.cmd == "tui":
        cfg = load_config()
        backend = make_backend(cfg)

        log.info("taskanov start...")
        log.info("Using Backend: {}".format(backend))

        logging.getLogger("taskanov.bg").setLevel(logging.DEBUG)

        ui_queue = queue.Queue()
        bg = TickerNotifier(backend, interval=60*5)
        bg.start()

        try:
            return run_tui(backend)
        except Exception as e:
            pass
        finally:
            bg.stop()
