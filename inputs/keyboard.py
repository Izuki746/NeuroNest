"""Keyboard backup input (Person C). Tiny Tk window, no admin rights needed.

SPACE down = onset, release (or 400 ms) = attempt, effort = 0.4 + 0.6*clip(hold/400).
S = send stim (excite on the selected region). R cycles region. K toggles excite/calm.

Run: python inputs/keyboard.py --server ws://127.0.0.1:8000/ws/input
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import queue
import threading
import time

log = logging.getLogger("keyboard")

REGIONS = [("lPMCDL", "excite"), ("lPMCVL", "excite"), ("lPMCM", "excite"), ("lM1", "excite"),
           ("lS1", "excite"), ("rM1", "calm"), ("rPMCDL", "calm")]


def now_ms() -> float:
    return time.monotonic() * 1000.0


def effort_from_hold(hold_ms: float) -> float:
    return 0.4 + 0.6 * max(0.0, min(1.0, hold_ms / 400.0))


class WS(threading.Thread):
    def __init__(self, url: str) -> None:
        super().__init__(daemon=True)
        self.url, self.q = url, queue.Queue()

    def send(self, m: dict) -> None:
        self.q.put(m)

    def run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        import websockets
        while True:
            try:
                async with websockets.connect(self.url) as ws:
                    await ws.send(json.dumps({"type": "hello", "client": "keyboard", "id": "kbd-1"}))
                    while True:
                        try:
                            m = self.q.get_nowait()
                        except queue.Empty:
                            await asyncio.sleep(0.005)
                            continue
                        try:
                            await ws.send(json.dumps(m))
                        except Exception:
                            self.q.put(m)
                            raise
            except Exception as e:  # noqa: BLE001
                log.warning("ws error %s, retry", e)
                await asyncio.sleep(1)


def main() -> None:
    import tkinter as tk

    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="ws://127.0.0.1:8000/ws/input")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    ws = WS(args.server)
    ws.start()

    root = tk.Tk()
    root.title("NeuroNest keyboard")
    root.geometry("420x220")
    info = tk.StringVar()
    status = tk.StringVar(value="SPACE = squeeze (hold longer = more effort)")
    idx = [0]
    state = {"down": False, "t0": 0.0, "after": None}

    def refresh():
        r, k = REGIONS[idx[0]]
        info.set(f"S = stim {k.upper()} on {r}   (R: next region)")

    def send_attempt():
        if not state["down"]:
            return
        hold = now_ms() - state["t0"]
        state["down"] = False
        e = effort_from_hold(hold)
        ws.send({"type": "attempt", "t_ms": state["t0"], "effort": round(e, 3), "depth": round(e, 3),
                 "speed": 0.7, "hold_ms": round(hold, 1), "source": "keyboard"})
        status.set(f"attempt sent, effort {int(e * 100)}%")

    def on_down(_e):
        if state["down"]:
            return  # key auto-repeat
        state["down"], state["t0"] = True, now_ms()
        ws.send({"type": "attempt_onset",
                "t_ms": state["t0"], "source": "keyboard"})
        state["after"] = root.after(400, send_attempt)

    def on_up(_e):
        if state["after"]:
            root.after_cancel(state["after"])
            state["after"] = None
        send_attempt()

    def on_s(_e):
        r, k = REGIONS[idx[0]]
        ws.send({"type": "stim", "region": r, "kind": k, "intensity": 1.0, "t_ms": now_ms(),
                 "id": "kbd-1"})
        status.set(f"stim {k} {r}")

    def on_r(_e):
        idx[0] = (idx[0] + 1) % len(REGIONS)
        refresh()

    tk.Label(root, textvariable=status, font=("Arial", 14)).pack(pady=20)
    tk.Label(root, textvariable=info, font=("Arial", 12)).pack(pady=10)
    refresh()
    root.bind("<KeyPress-space>", on_down)
    root.bind("<KeyRelease-space>", on_up)
    root.bind("s", on_s)
    root.bind("r", on_r)
    root.mainloop()


if __name__ == "__main__":
    main()
