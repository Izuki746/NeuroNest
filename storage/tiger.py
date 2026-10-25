"""Tiger Data (TimescaleDB/Postgres) writer. Thread-safe, batched, fail-soft (Person C).

    python -m storage.tiger --init      # create tables, hypertables, continuous aggregate
    python -m storage.tiger --replay    # upload rows buffered in storage/offline/*.csv
    python -m storage.tiger --check     # connectivity + row counts

Never touches the 20 Hz loop: callers only append to an in-memory queue; a background
thread flushes every second. If the DB is unreachable rows go to storage/offline/<table>.csv.
Timestamps: UTC datetime.now(timezone.utc). Never local time.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("tiger")

HERE = Path(__file__).parent
OFFLINE = HERE / "offline"
SCHEMA = HERE / "schema.sql"

# table -> [(column, type)]; types: ts text int real json arr
TABLES: dict[str, list[tuple[str, str]]] = {
    "sessions": [("session_id", "text"), ("started", "ts"), ("card", "text"), ("age", "int"),
                 ("lesion", "json"), ("traits", "json"), ("kind", "text")],
    "brain_state": [("time", "ts"), ("session_id", "text"), ("sim_day", "int"), ("activity", "arr")],
    "attempts": [("time", "ts"), ("session_id", "text"), ("sim_day", "int"), ("effort", "real"),
                 ("depth", "real"), ("speed", "real"), ("hold_ms",
                                                        "real"), ("arm_output", "real"),
                 ("arm_function", "real"), ("stim_offset_ms",
                                            "real"), ("stim_verdict", "text"),
                 ("gate", "real"), ("source", "text")],
    "stimulation": [("time", "ts"), ("session_id", "text"), ("sim_day", "int"), ("actor", "text"),
                    ("region", "text"), ("kind", "text"), ("intensity", "real"), ("offset_ms", "real")],
    "daily": [("time", "ts"), ("session_id", "text"), ("sim_day", "int"), ("arm_function", "real"),
              ("mean_effort", "real"), ("attempts",
                                        "int"), ("window_level", "real"),
              ("stim_count", "int"), ("plan", "json")],
    "weights": [("time", "ts"), ("session_id", "text"), ("sim_day", "int"), ("src", "int"),
                ("dst", "int"), ("strength", "real")],
    "forecasts": [("time", "ts"), ("session_id", "text"), ("sim_day", "int"), ("p10", "real"),
                  ("p50", "real"), ("p90", "real"), ("n_alive", "int"), ("method", "text")],
}
FLUSH_ORDER = ["sessions", "brain_state", "attempts",
               "stimulation", "daily", "weights", "forecasts"]
HYPERTABLES = [t for t in TABLES if t != "sessions"]
MAX_QUEUE = 100_000


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _f(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _jsonable(o):
    if hasattr(o, "tolist"):
        return o.tolist()
    if hasattr(o, "item"):
        return o.item()
    if hasattr(o, "__dict__"):
        return o.__dict__
    return str(o)


def _insert_sql(table: str) -> str:
    cols = [c for c, _ in TABLES[table]]
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
    if table == "sessions":
        sql += " ON CONFLICT (session_id) DO NOTHING"
    return sql


def _adapt(table: str, row: tuple) -> tuple:
    """Wrap json columns for psycopg."""
    from psycopg.types.json import Jsonb
    out = []
    for (col, typ), v in zip(TABLES[table], row):
        if typ == "json" and v is not None:
            v = Jsonb(v, dumps=lambda o: json.dumps(o, default=_jsonable))
        out.append(v)
    return tuple(out)


class TigerWriter:
    def __init__(self, url: str | None) -> None:
        self.url = url or None
        self._q: deque[tuple[str, tuple]] = deque(maxlen=MAX_QUEUE)
        self._lock = threading.Lock()
        self._conn = None
        self._next_retry = 0.0
        self._stop_ev = threading.Event()
        self._last_weights_day: dict[str, int] = {}
        self._last_brain_t = 0.0
        self._stats: dict = {}
        self._stats_t = 0.0
        self.online = False
        OFFLINE.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="tiger-flush")
        self._thread.start()

    # ------------------------------------------------------------ public API
    def _put(self, table: str, row: tuple) -> None:
        with self._lock:
            self._q.append((table, row))

    def session(self, session_id, card, age, lesion, traits, kind="live") -> None:
        self._put("sessions", (str(session_id), utcnow(),
                  card, _i(age), lesion, traits, kind))

    def attempt(self, session_id, sim_day, result, source) -> None:
        def g(k): return getattr(result, k, None)  # noqa: E731
        self._put("attempts", (utcnow(), str(session_id), _i(sim_day), _f(g("effort")), None, None, None,
                               _f(g("arm_output")), _f(g("arm_function")), _f(
                                   g("stim_offset_ms")),
                               g("stim_verdict"), _f(g("gate")), source))

    def attempt_full(self, session_id, sim_day, effort, depth, speed, hold_ms, arm_output, arm_function,
                     stim_offset_ms, stim_verdict, gate, source) -> None:
        """For callers that have depth/speed/hold (AttemptResult itself carries only effort)."""
        self._put("attempts", (utcnow(), str(session_id), _i(sim_day), _f(effort), _f(depth), _f(speed),
                               _f(hold_ms), _f(arm_output), _f(
                                   arm_function), _f(stim_offset_ms),
                               stim_verdict, _f(gate), source))

    def stim(self, session_id, sim_day, actor, region, kind, intensity, offset_ms=None) -> None:
        self._put("stimulation", (utcnow(), str(session_id), _i(sim_day), actor, region, kind,
                                  _f(intensity), _f(offset_ms)))

    def daily(self, session_id, day_result, plan=None) -> None:
        def g(k): return getattr(day_result, k, None)  # noqa: E731
        self._put("daily", (utcnow(), str(session_id), _i(g("day")), _f(g("arm_function")),
                            _f(g("mean_effort")), _i(
                                g("attempts")), _f(g("window_level")),
                            _i(g("stim_count")), plan))

    def brain_state(self, session_id, sim_day, activity) -> None:
        # ~5 Hz when online; 1 Hz when offline so the CSV buffer stays small
        now = time.monotonic()
        if now - self._last_brain_t < (0.19 if self.online else 0.95):
            return
        self._last_brain_t = now
        vals = [round(float(x), 4) for x in activity]
        self._put("brain_state", (utcnow(), str(session_id), _i(sim_day), vals))

    def weights(self, session_id, sim_day, W_delta, threshold: float = 1e-3, max_edges: int = 400) -> None:
        """Changed edges only, at most once per sim_day per session (keeps volume sane)."""
        key = str(session_id)
        day = _i(sim_day) or 0
        if self._last_weights_day.get(key) == day:
            return
        self._last_weights_day[key] = day
        import numpy as np
        W = np.asarray(W_delta, dtype=np.float32)
        idx = np.argwhere(np.abs(W) > threshold)
        if idx.size == 0:
            return
        if len(idx) > max_edges:
            mags = np.abs(W[idx[:, 0], idx[:, 1]])
            idx = idx[np.argsort(-mags)[:max_edges]]
        t = utcnow()
        for s, d in idx:
            self._put("weights", (t, key, day, int(s), int(d), float(W[s, d])))

    def forecast(self, session_id, sim_day, fc: dict, method: str = "twins") -> None:
        self._put("forecasts", (utcnow(), str(session_id), _i(sim_day), _f(fc.get("p10")), _f(fc.get("p50")),
                                _f(fc.get("p90")), _i(fc.get("n_alive")), method))

    def flush(self) -> None:
        self._flush_once()

    def stats(self) -> dict:
        """Cached by the background thread (never blocks the caller)."""
        return dict(self._stats)

    def close(self) -> None:
        self._stop_ev.set()
        self._flush_once()

    # ------------------------------------------------------------ internals
    def _run(self) -> None:
        while not self._stop_ev.wait(1.0):
            try:
                self._flush_once()
                if time.monotonic() - self._stats_t > 10:
                    self._stats_t = time.monotonic()
                    self._refresh_stats()
            except Exception as e:  # noqa: BLE001
                log.warning("flush loop error: %s", e)

    def _connect(self):
        if not self.url:
            return None
        if self._conn is not None:
            return self._conn
        if time.monotonic() < self._next_retry:
            return None
        try:
            import psycopg
            self._conn = psycopg.connect(self.url, connect_timeout=5)
            self.online = True
            log.info("Tiger Data connected")
            self._replay_offline(self._conn)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "Tiger connect failed (%s); buffering to CSV, retry in 30 s", e)
            self._conn, self.online = None, False
            self._next_retry = time.monotonic() + 30
        return self._conn

    def _drain(self) -> dict[str, list[tuple]]:
        with self._lock:
            items = list(self._q)
            self._q.clear()
        by: dict[str, list[tuple]] = {}
        for t, r in items:
            by.setdefault(t, []).append(r)
        return by

    def _flush_once(self) -> None:
        by = self._drain()
        if not by:
            return
        conn = self._connect()
        if conn is None:
            self._to_csv(by)
            return
        try:
            with conn.cursor() as cur:
                for table in FLUSH_ORDER:
                    rows = by.get(table)
                    if rows:
                        cur.executemany(_insert_sql(table), [
                                        _adapt(table, r) for r in rows])
            conn.commit()
        except Exception as e:  # noqa: BLE001
            log.warning("Tiger write failed (%s); buffering batch to CSV", e)
            try:
                conn.rollback()
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn, self.online = None, False
            self._next_retry = time.monotonic() + 30
            self._to_csv(by)

    def _to_csv(self, by: dict[str, list[tuple]]) -> None:
        for table, rows in by.items():
            path = OFFLINE / f"{table}.csv"
            new = not path.exists()
            with open(path, "a", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                cols = TABLES[table]
                if new:
                    w.writerow([c for c, _ in cols])
                for r in rows:
                    cells = []
                    for (c, typ), v in zip(cols, r):
                        if v is None:
                            cells.append("")
                        elif typ == "ts":
                            cells.append(v.isoformat())
                        elif typ in ("json", "arr"):
                            cells.append(json.dumps(v, default=_jsonable))
                        else:
                            cells.append(v)
                    w.writerow(cells)

    def _replay_offline(self, conn) -> None:
        n = replay_csv(conn)
        if n:
            log.info("replayed %d buffered rows from storage/offline", n)

    def _refresh_stats(self) -> None:
        conn = self._conn
        if conn is None:
            return
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM attempts")
                a = cur.fetchone()[0]
                cur.execute(
                    "SELECT count(*) FROM sessions WHERE kind = 'population'")
                p = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM brain_state")
                b = cur.fetchone()[0]
            conn.commit()
            self._stats = {"total_attempts": a,
                           "simulated_patients": p, "brain_state_samples": b}
        except Exception as e:  # noqa: BLE001
            log.warning("stats failed: %s", e)
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass


# ------------------------------------------------------------------ module-level helpers
def _parse(typ: str, s: str):
    if s == "":
        return None
    if typ == "ts":
        return datetime.fromisoformat(s)
    if typ == "int":
        return int(float(s))
    if typ == "real":
        return float(s)
    if typ in ("json", "arr"):
        return json.loads(s)
    return s


def replay_csv(conn) -> int:
    """Upload and delete storage/offline/*.csv. Returns row count."""
    total = 0
    for table in FLUSH_ORDER:
        path = OFFLINE / f"{table}.csv"
        if not path.exists():
            continue
        with open(path, newline="", encoding="utf-8") as fh:
            rows = [tuple(_parse(t, rec[c]) for c, t in TABLES[table])
                    for rec in csv.DictReader(fh)]
        if rows:
            with conn.cursor() as cur:
                cur.executemany(_insert_sql(table), [
                                _adapt(table, r) for r in rows])
            conn.commit()
            total += len(rows)
        path.unlink()
    return total


def bulk_write_population(sessions: list[dict], daily_rows: list[tuple], url: str | None = None) -> dict:
    """For Person A's ai/population.py. Uses COPY.

    sessions:   dicts with keys session_id, card, age, lesion, traits, (optional) kind (default 'population')
    daily_rows: tuples (time, session_id, sim_day, arm_function, mean_effort, attempts, window_level,
                stim_count, plan). `time` should be the synthetic timestamp 2026-01-01 + sim_day days
                (tz-aware datetime). plan may be None / dict / JSON string.
    Returns {"sessions": n, "daily": n, "ok": bool}. Never raises: falls back to offline CSV.
    """
    url = url or os.getenv("TIGER_DATABASE_URL")
    out = {"sessions": 0, "daily": 0, "ok": False}
    try:
        if not url:
            raise RuntimeError("TIGER_DATABASE_URL not set")
        import psycopg
        from psycopg.types.json import Jsonb
        with psycopg.connect(url, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                with cur.copy("COPY sessions (session_id, started, card, age, lesion, traits, kind) FROM STDIN") as cp:
                    for s in sessions:
                        started = s.get("started") or datetime(
                            2026, 1, 1, tzinfo=timezone.utc)
                        cp.write_row((s["session_id"], started, s.get("card"), s.get("age"),
                                      Jsonb(s.get("lesion")), Jsonb(s.get("traits")), s.get("kind", "population")))
                with cur.copy("COPY daily (time, session_id, sim_day, arm_function, mean_effort, attempts, "
                              "window_level, stim_count, plan) FROM STDIN") as cp:
                    for r in daily_rows:
                        plan = r[8] if len(r) > 8 else None
                        if isinstance(plan, str):
                            plan = json.loads(plan)
                        cp.write_row(
                            (*r[:8], Jsonb(plan) if plan is not None else None))
            conn.commit()
        out.update(sessions=len(sessions), daily=len(daily_rows), ok=True)
    except Exception as e:  # noqa: BLE001
        log.warning("bulk_write_population failed (%s); buffering to CSV", e)
        w = TigerWriter(None)
        for s in sessions:
            w._put("sessions", (s["session_id"], s.get("started") or datetime(2026, 1, 1, tzinfo=timezone.utc),
                                s.get("card"), _i(s.get("age")), s.get(
                                    "lesion"), s.get("traits"),
                                s.get("kind", "population")))
        for r in daily_rows:
            w._put("daily", (*r[:8], r[8] if len(r) > 8 else None))
        w.close()
    return out


# ------------------------------------------------------------------ CLI
HYPERTABLE_SQL = [
    "SELECT create_hypertable('{t}', by_range('time'), if_not_exists => TRUE)",
    "SELECT create_hypertable('{t}', 'time', if_not_exists => TRUE)",
]
CAGG_SQL = """
CREATE MATERIALIZED VIEW IF NOT EXISTS attempts_per_minute
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 minute', time) AS bucket,
       count(*) AS n,
       avg(effort) AS avg_effort,
       avg(arm_output) AS avg_arm_output
FROM attempts
GROUP BY bucket
WITH NO DATA
"""
CAGG_POLICY_SQL = """
SELECT add_continuous_aggregate_policy('attempts_per_minute',
  start_offset => INTERVAL '1 hour', end_offset => INTERVAL '1 minute',
  schedule_interval => INTERVAL '1 minute', if_not_exists => TRUE)
"""


def init_db(url: str) -> None:
    import psycopg
    # autocommit: create_hypertable fallbacks and continuous aggregates cannot run in a transaction block
    with psycopg.connect(url, connect_timeout=10, autocommit=True) as conn:
        sql = "\n".join(l for l in SCHEMA.read_text().splitlines()
                        if not l.strip().startswith("--"))
        sql = "\n".join(l.split("--")[0] for l in sql.splitlines())
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            conn.execute(stmt)
        print("tables created")
        try:
            conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        except Exception as e:  # noqa: BLE001
            print(f"  (extension note: {e})")
        for t in HYPERTABLES:
            for form in HYPERTABLE_SQL:
                try:
                    conn.execute(form.format(t=t))
                    print(f"hypertable: {t}")
                    break
                except Exception as e:  # noqa: BLE001
                    last = e
            else:
                print(f"  WARNING: could not make {t} a hypertable: {last}")
        for label, stmt in (("continuous aggregate", CAGG_SQL), ("refresh policy", CAGG_POLICY_SQL)):
            try:
                conn.execute(stmt)
                print(f"{label}: ok")
            except Exception as e:  # noqa: BLE001
                print(f"  WARNING: {label} failed: {e}")


def check_db(url: str) -> None:
    import psycopg
    with psycopg.connect(url, connect_timeout=10) as conn:
        for t in TABLES:
            n = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            print(f"{t:12s} {n}")


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true",
                    help="write sample rows (to DB if configured, else CSV)")
    args = ap.parse_args()
    url = os.getenv("TIGER_DATABASE_URL")
    if args.init or args.replay or args.check:
        if not url:
            raise SystemExit(
                "TIGER_DATABASE_URL is not set (copy the connection string from Tiger Cloud into .env)")
    if args.init:
        init_db(url)
    if args.replay:
        import psycopg
        with psycopg.connect(url, connect_timeout=10) as conn:
            print(f"replayed {replay_csv(conn)} rows")
    if args.check:
        check_db(url)
    if args.selftest:
        w = TigerWriter(url)
        w.session("selftest", "B", 45, {"lM1": 0.5}, {"hidden": True}, "live")
        w.stim("selftest", 1, "human", "lPMCDL", "excite", 1.0, -80.0)
        w.forecast("selftest", 1, {
                   "p10": 0.6, "p50": 0.7, "p90": 0.8, "n_alive": 150}, "twins")
        w.close()
        print("selftest rows written",
              "to DB" if w.online else "to storage/offline/")


if __name__ == "__main__":
    main()
