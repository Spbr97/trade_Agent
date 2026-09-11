"""SQLite journal (PLAN.md 8): signals and their transitions, alerts, real positions and
fills, paper trades, daily/weekly state, data health, tags (rule breaks, FOMO, revenge).

Money is stored as REAL for querying convenience; the cost calculator (Decimal) produced
the numbers, this table only remembers them. Everything the weekly review, the stats and
the dashboard need is in here.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from tradedesk.backtest.fills import Fill, Position
from tradedesk.backtest.portfolio import ClosedTrade
from tradedesk.broker.indstocks.models import IST
from tradedesk.engine.lifecycle import TrackedSignal
from tradedesk.engine.signals import ExitPlan, SetupKind, Signal
from tradedesk.live.models import Alert

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY, scrip_code TEXT, symbol TEXT, setup TEXT, armed_on TEXT,
    trigger REAL, stop REAL, t1 REAL, t2 REAL, atr REAL, state TEXT, grade TEXT, score INTEGER,
    signal_json TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS transitions (
    signal_id TEXT, on_date TEXT, from_state TEXT, to_state TEXT, note TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
    at TEXT, kind TEXT, level TEXT, scrip_code TEXT, symbol TEXT, message TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS positions (
    signal_id TEXT PRIMARY KEY, scrip_code TEXT, symbol TEXT, setup TEXT, entry_date TEXT,
    entry_price REAL, qty_initial INTEGER, qty_open INTEGER, stop REAL, partial_done INTEGER,
    highest_close REAL, sessions_held INTEGER, source TEXT, fills_json TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS stop_updates (
    signal_id TEXT, on_date TEXT, old_stop REAL, new_stop REAL, reason TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    signal_id TEXT PRIMARY KEY, scrip_code TEXT, symbol TEXT, setup TEXT, entry_date TEXT,
    exit_date TEXT, qty INTEGER, entry_price REAL, exit_reason TEXT, gross_pnl REAL, costs REAL,
    net_pnl REAL, r_multiple REAL, sessions_held INTEGER, gap_damage REAL, source TEXT,
    fills_json TEXT
);
CREATE TABLE IF NOT EXISTS paper_trades (
    signal_id TEXT PRIMARY KEY, scrip_code TEXT, symbol TEXT, setup TEXT, entry_date TEXT,
    exit_date TEXT, qty INTEGER, entry_price REAL, exit_reason TEXT, gross_pnl REAL, costs REAL,
    net_pnl REAL, r_multiple REAL, sessions_held INTEGER, gap_damage REAL, fills_json TEXT
);
CREATE TABLE IF NOT EXISTS daily_state (
    on_date TEXT PRIMARY KEY, equity REAL, open_positions INTEGER, heat_pct REAL,
    entries INTEGER, regime TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS weekly_state (
    week TEXT PRIMARY KEY, start_equity REAL, end_equity REAL, loss_limit_hit INTEGER,
    trades INTEGER, net_pnl REAL
);
CREATE TABLE IF NOT EXISTS data_health (
    at TEXT, kind TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS tags (
    signal_id TEXT, tag TEXT, note TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS order_updates (
    at TEXT, order_id TEXT, status TEXT, filled_qty INTEGER, average_price REAL, raw TEXT
);
"""


def _now() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


@dataclass
class Journal:
    path: Path | str = ":memory:"

    def __post_init__(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(self.path))
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> Journal:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------- signals

    def upsert_signal(
        self, ts: TrackedSignal, *, grade: str | None = None, score: int | None = None
    ) -> None:
        s = ts.signal
        with self.con:
            self.con.execute(
                """INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET state=excluded.state,
                   grade=COALESCE(excluded.grade, signals.grade),
                   score=COALESCE(excluded.score, signals.score), updated_at=excluded.updated_at""",
                (
                    s.id,
                    s.scrip_code,
                    s.symbol,
                    s.setup.value,
                    s.armed_on.isoformat(),
                    s.trigger,
                    s.stop,
                    s.t1,
                    s.t2,
                    s.atr,
                    ts.state.value,
                    grade,
                    score,
                    s.model_dump_json(),
                    _now(),
                ),  # fmt: skip
            )
            known = {
                (r["to_state"], r["on_date"])
                for r in self.con.execute(
                    "SELECT to_state, on_date FROM transitions WHERE signal_id = ?", (s.id,)
                )
            }
            for h in ts.history:
                if (h.to_state.value, h.on.isoformat()) not in known:
                    self.con.execute(
                        "INSERT INTO transitions VALUES (?,?,?,?,?,?)",
                        (
                            s.id,
                            h.on.isoformat(),
                            h.from_state.value,
                            h.to_state.value,
                            h.note,
                            _now(),
                        ),
                    )

    def load_signal(self, signal_id: str) -> TrackedSignal | None:
        row = self.con.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
        if row is None:
            return None
        from tradedesk.engine.lifecycle import SignalState, Transition

        ts = TrackedSignal(signal=Signal.model_validate_json(row["signal_json"]))
        for r in self.con.execute(
            "SELECT * FROM transitions WHERE signal_id = ? ORDER BY rowid", (signal_id,)
        ):
            ts.history.append(
                Transition(
                    on=date.fromisoformat(r["on_date"]),
                    from_state=SignalState(r["from_state"]),
                    to_state=SignalState(r["to_state"]),
                    note=r["note"] or "",
                )
            )
        ts.state = SignalState(row["state"])
        return ts

    def signals_on(self, armed_on: date) -> list[TrackedSignal]:
        ids = [
            r["id"]
            for r in self.con.execute(
                "SELECT id FROM signals WHERE armed_on = ? ORDER BY id", (armed_on.isoformat(),)
            )
        ]
        return [t for i in ids if (t := self.load_signal(i)) is not None]

    def triggered_between(
        self, start: date, end: date
    ) -> list[tuple[TrackedSignal, date, float | None]]:
        """Signals with a TRIGGERED transition in [start, end], with the day and fill price."""
        out = []
        rows = self.con.execute(
            """SELECT signal_id, on_date, note FROM transitions
               WHERE to_state = 'triggered' AND on_date BETWEEN ? AND ?
               ORDER BY on_date, signal_id""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        for r in rows:
            ts = self.load_signal(r["signal_id"])
            if ts is None:
                continue
            fill = _fill_from_note(r["note"] or "")
            out.append((ts, date.fromisoformat(r["on_date"]), fill))
        return out

    # -------------------------------------------------------------- alerts

    def record_alert(self, a: Alert) -> None:
        with self.con:
            self.con.execute(
                "INSERT INTO alerts VALUES (?,?,?,?,?,?,?)",
                (
                    a.at.isoformat(),
                    a.kind.value,
                    a.level.value,
                    a.scrip_code,
                    a.symbol,
                    a.message,
                    json.dumps(a.payload, default=str),
                ),  # fmt: skip
            )

    def alerts_on(self, on: date) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT * FROM alerts WHERE substr(at, 1, 10) = ? ORDER BY at", (on.isoformat(),)
        ).fetchall()

    # ----------------------------------------------------------- positions

    def save_position(self, pos: Position, *, source: str = "live") -> None:
        s = pos.signal
        with self.con:
            self.con.execute(
                """INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    s.id,
                    s.scrip_code,
                    s.symbol,
                    s.setup.value,
                    pos.entry_date.isoformat(),
                    pos.entry_price,
                    pos.qty_initial,
                    pos.qty_open,
                    pos.stop,
                    int(pos.partial_done),
                    pos.highest_close,
                    pos.sessions_held,
                    source,
                    json.dumps([_fill_dict(f) for f in pos.fills]),
                    _now(),
                ),  # fmt: skip
            )
        self.upsert_signal(TrackedSignal(signal=s)) if self.load_signal(s.id) is None else None

    def record_stop_update(
        self, signal_id: str, on: date, old: float, new: float, reason: str
    ) -> None:
        with self.con:
            self.con.execute(
                "INSERT INTO stop_updates VALUES (?,?,?,?,?)",
                (signal_id, on.isoformat(), old, new, reason),
            )

    def open_positions(self, source: str = "live") -> list[Position]:
        rows = self.con.execute(
            "SELECT * FROM positions WHERE qty_open > 0 AND source = ? ORDER BY entry_date",
            (source,),
        ).fetchall()
        return [self._position_from_row(r) for r in rows]

    def _position_from_row(self, r: sqlite3.Row) -> Position:
        sig_row = self.con.execute(
            "SELECT signal_json FROM signals WHERE id = ?", (r["signal_id"],)
        ).fetchone()
        signal = (
            Signal.model_validate_json(sig_row["signal_json"])
            if sig_row
            else Signal(
                id=r["signal_id"],
                scrip_code=r["scrip_code"],
                symbol=r["symbol"],
                setup=SetupKind(r["setup"]),
                armed_on=date.fromisoformat(r["entry_date"]),
                trigger=r["entry_price"],
                stop=r["stop"],
                t1=r["entry_price"],
                t2=r["entry_price"],
                atr=0.0,
                exit_plan=ExitPlan(),
            )  # fmt: skip
        )
        fills = [_fill_from_dict(d) for d in json.loads(r["fills_json"] or "[]")]
        return Position(
            signal=signal,
            entry_date=date.fromisoformat(r["entry_date"]),
            entry_price=float(r["entry_price"]),
            qty_initial=int(r["qty_initial"]),
            qty_open=int(r["qty_open"]),
            stop=float(r["stop"]),
            partial_done=bool(r["partial_done"]),
            highest_close=float(r["highest_close"]),
            sessions_held=int(r["sessions_held"]),
            fills=fills,
        )

    # -------------------------------------------------------------- trades

    def record_trade(self, t: ClosedTrade, *, source: str = "live") -> None:
        table = "paper_trades" if source == "paper" else "trades"
        p = t.position
        cols = (
            p.signal.id, t.scrip_code, p.signal.symbol, t.setup, t.entry_date.isoformat(),
            t.exit_date.isoformat(), p.qty_initial, p.entry_price, t.exit_reason, p.gross_pnl(),
            t.costs, t.net_pnl, t.r_multiple, t.sessions_held, t.gap_damage,
        )  # fmt: skip
        fills = json.dumps([_fill_dict(f) for f in p.fills])
        with self.con:
            if table == "trades":
                self.con.execute(
                    f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (*cols, source, fills),
                )
            else:
                self.con.execute(
                    f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (*cols, fills),
                )
            self.con.execute(
                "UPDATE positions SET qty_open = 0, updated_at = ? "
                "WHERE signal_id = ? AND source = ?",
                (_now(), p.signal.id, source),
            )

    def trades(
        self, *, source: str = "live", setup: str | None = None, last: int | None = None
    ) -> list[sqlite3.Row]:
        table = "paper_trades" if source == "paper" else "trades"
        sql = f"SELECT * FROM {table}"
        params: list[Any] = []
        if setup:
            sql += " WHERE setup = ?"
            params.append(setup)
        sql += " ORDER BY exit_date DESC, signal_id DESC"
        if last:
            sql += " LIMIT ?"
            params.append(last)
        return self.con.execute(sql, params).fetchall()

    # --------------------------------------------------------- state / misc

    def record_daily_state(self, on: date, **fields: Any) -> None:
        keys = ("equity", "open_positions", "heat_pct", "entries", "regime", "note")
        with self.con:
            self.con.execute(
                "INSERT OR REPLACE INTO daily_state VALUES (?,?,?,?,?,?,?)",
                (on.isoformat(), *(fields.get(k) for k in keys)),
            )

    def record_weekly_state(self, week: str, **fields: Any) -> None:
        keys = ("start_equity", "end_equity", "loss_limit_hit", "trades", "net_pnl")
        with self.con:
            self.con.execute(
                "INSERT OR REPLACE INTO weekly_state VALUES (?,?,?,?,?,?)",
                (week, *(fields.get(k) for k in keys)),
            )

    def record_health(self, kind: str, detail: str) -> None:
        with self.con:
            self.con.execute("INSERT INTO data_health VALUES (?,?,?)", (_now(), kind, detail))

    def tag(self, signal_id: str, tag: str, note: str = "") -> None:
        with self.con:
            self.con.execute("INSERT INTO tags VALUES (?,?,?,?)", (signal_id, tag, note, _now()))

    def tags_for(self, signal_ids: Iterable[str] | None = None) -> list[sqlite3.Row]:
        if signal_ids is None:
            return self.con.execute("SELECT * FROM tags ORDER BY at").fetchall()
        ids = list(signal_ids)
        if not ids:
            return []
        q = ",".join("?" * len(ids))
        return self.con.execute(
            f"SELECT * FROM tags WHERE signal_id IN ({q}) ORDER BY at", ids
        ).fetchall()

    def record_order_update(self, msg: dict[str, Any]) -> None:
        with self.con:
            self.con.execute(
                "INSERT INTO order_updates VALUES (?,?,?,?,?,?)",
                (
                    _now(),
                    str(msg.get("order_id", "")),
                    str(msg.get("order_status", "")),
                    msg.get("filled_quantity"),
                    msg.get("average_price"),
                    json.dumps(msg, default=str),
                ),  # fmt: skip
            )

    def order_updates(self, last: int = 50) -> Sequence[sqlite3.Row]:
        return self.con.execute(
            "SELECT * FROM order_updates ORDER BY rowid DESC LIMIT ?", (last,)
        ).fetchall()


def _fill_dict(f: Fill) -> dict[str, Any]:
    return {"on": f.on.isoformat(), "price": f.price, "qty": f.qty, "reason": f.reason.value}


def _fill_from_dict(d: dict[str, Any]) -> Fill:
    from tradedesk.backtest.fills import FillReason

    return Fill(
        on=date.fromisoformat(d["on"]),
        price=float(d["price"]),
        qty=int(d["qty"]),
        reason=FillReason(d["reason"]),
    )


def _fill_from_note(note: str) -> float | None:
    """Transition notes look like 'filled 465.97' or '15m close 100.90 > 100.00'."""
    import re

    m = re.search(r"(?:filled|close)\s+([0-9]+(?:\.[0-9]+)?)", note)
    return float(m.group(1)) if m else None
