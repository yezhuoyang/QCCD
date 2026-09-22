"""The delivery worker: moves queued prompts to the agent runtimes that can take them.

It runs inside the service.  Each pass:

* a queued delivery to a Codex (`appserver`) session is claimed (queued -> sending) and
  handed to the session's bridge: `accepted` with a turn id, `busy` (put back, retried when
  the turn completes), or an error -- which is retried with backoff and, once attempts
  run out, `failed`.  A send whose outcome is unknown (timeout, dropped connection) becomes
  `uncertain`, and before anything is sent again the bridge lists the thread's turns and
  looks for the delivery id: found means `accepted`, not found means safe to retry.
* `channel` (Claude Code) deliveries are claimed by the MCP process that holds the channel;
  `pull` deliveries wait for the agent to read them.  Neither is touched here.
* sessions nothing has heard from for a while are marked disconnected, so Studio never
  shows a dead agent as connected.

Delivery is at-least-once with safeguards: idempotency keys on change sets, jobs and
submissions mean a duplicated prompt cannot duplicate a committed mutation.
"""

from __future__ import annotations

import threading
import time

__all__ = ["DeliveryWorker", "MAX_ATTEMPTS"]

MAX_ATTEMPTS = 6


class DeliveryWorker:
    def __init__(self, state, interval: float = 1.0):
        self.state = state
        self.ws = state.ws
        self.interval = interval
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="qccd-delivery", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        for br in list(self.state.bridges.values()):
            try:
                br.close()
            except Exception:
                pass

    def kick(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        last_liveness = 0.0
        while not self._stop.is_set():
            try:
                self.pass_once()
                if time.time() - last_liveness > 10:
                    self._liveness()
                    last_liveness = time.time()
            except Exception as exc:  # the worker must survive a bad row
                try:
                    self.ws.emit("delivery.worker_error", {"error": f"{type(exc).__name__}: {exc}"})
                except Exception:
                    pass
            self._wake.wait(self.interval)
            self._wake.clear()

    def pass_once(self) -> int:
        n = 0
        ws = self.ws
        # 1. reconcile uncertain appserver deliveries first: never resend blindly
        for r in ws.store.all("SELECT d.*, s.mode FROM deliveries d JOIN sessions s ON s.id=d.session_id "
                              "WHERE d.state='uncertain' AND s.mode='appserver'"):
            br = self.state.bridges.get(r["session_id"])
            if br is None or not br.connected:
                continue
            found = br.reconcile(r["id"])
            if found is True:
                ws.mark_delivery(r["id"], "accepted", detail="reconciled: the thread holds this delivery")
            elif found is False:
                ws.mark_delivery(r["id"], "queued", detail="reconciled: not in the thread; will retry")
        # 2. deliver what is due
        for d in ws.due_deliveries(limit=20):
            s = ws.session(d["session_id"])
            if s["mode"] != "appserver":
                continue
            if s["write_fence"]:
                continue
            br = self.state.bridges.get(s["id"])
            if br is None or not br.connected:
                continue          # stays queued until the bridge reconnects
            claimed = ws.claim_delivery(d["id"])
            if not claimed:
                continue
            payload = ws.delivery_payload(claimed)
            try:
                out = br.deliver(payload["text"], claimed["id"], steer=claimed["kind"] == "steer")
            except Exception as exc:
                uncertain = bool(getattr(exc, "error", {}).get("uncertain")) if hasattr(exc, "error") else False
                if uncertain or not br.connected:
                    ws.mark_delivery(claimed["id"], "uncertain", error=str(exc))
                elif claimed["attempts"] >= MAX_ATTEMPTS:
                    ws.mark_delivery(claimed["id"], "failed", error=str(exc))
                else:
                    ws.mark_delivery(claimed["id"], "queued", error=str(exc),
                                     retry_in=min(60.0, 2.0 ** claimed["attempts"]))
                continue
            if out["state"] == "busy":
                ws.mark_delivery(claimed["id"], "queued",
                                 detail=f"the agent is busy (turn {out.get('active_turn')}); queued behind it",
                                 retry_in=2.0)
                # attempts count failures, not waiting
                with ws.store.lock:
                    ws.store.db.execute("UPDATE deliveries SET attempts=attempts-1 WHERE id=?", (claimed["id"],))
                continue
            ws.mark_delivery(claimed["id"], "accepted", external_ref=out.get("turn_id"),
                             detail=f"{out.get('how')} accepted by Codex")
            n += 1
        return n

    def _liveness(self) -> None:
        ws = self.ws
        now = time.time()
        for s in ws.sessions():
            if s["status"] != "connected":
                continue
            if s["mode"] == "appserver":
                br = self.state.bridges.get(s["id"])
                if br is None or not br.connected:
                    ws.session_status(s["id"], "disconnected", detail="the Codex bridge is not connected")
            elif now - s["last_seen"] > 90:
                ws.session_status(s["id"], "disconnected", detail="no heartbeat for 90 s")
