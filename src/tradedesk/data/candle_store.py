"""DuckDB-backed store for candles, instruments, corporate actions and results dates.

Candles are stored exactly as the API returned them (`ts` = candle open, epoch seconds IST).

Adjustment policy: the INDstocks feed ALREADY serves split/bonus-adjusted history, so the
store does NOT re-apply corporate-action factors by default - see
`CandleStore.__init__(apply_corporate_actions=...)`. Factors are only ever applied on read,
never written back, so a feed that does serve unadjusted prices can opt in without a reload.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from fractions import Fraction
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from tradedesk.broker.indstocks.models import IST, Candle, IndexInstrument, Instrument, Interval
from tradedesk.data.models import ActionKind, CorporateAction, ResultsEvent

CANDLE_COLUMNS = ["open", "high", "low", "close", "volume"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    scrip_code VARCHAR NOT NULL,
    interval   VARCHAR NOT NULL,
    ts         BIGINT  NOT NULL,
    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT,
    PRIMARY KEY (scrip_code, interval, ts)
);
CREATE TABLE IF NOT EXISTS instruments (
    scrip_code VARCHAR PRIMARY KEY,
    exch VARCHAR, security_id VARCHAR, kind VARCHAR,
    trading_symbol VARCHAR, symbol_name VARCHAR, series VARCHAR, instrument_name VARCHAR,
    tick_size DOUBLE, lot_units INTEGER, name VARCHAR
);
CREATE TABLE IF NOT EXISTS corporate_actions (
    symbol VARCHAR NOT NULL, ex_date DATE NOT NULL, kind VARCHAR NOT NULL,
    factor_num INTEGER, factor_den INTEGER, purpose VARCHAR, source VARCHAR,
    PRIMARY KEY (symbol, ex_date, kind, purpose)
);
CREATE TABLE IF NOT EXISTS results_events (
    symbol VARCHAR NOT NULL, event_date DATE NOT NULL, purpose VARCHAR, source VARCHAR,
    PRIMARY KEY (symbol, event_date, purpose)
);
"""


def ist_dates(df: pd.DataFrame) -> list[date]:
    """Calendar dates (IST) of each bar's open time."""
    return list(pd.DatetimeIndex(df.index).tz_convert("Asia/Kolkata").date)


def _epoch(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return int(dt.timestamp())


class CandleStore:
    def __init__(
        self, path: Path | str = ":memory:", *, apply_corporate_actions: bool = False
    ) -> None:
        """`apply_corporate_actions` controls whether `load(adjusted=True)` actually rescales
        prices from the corporate_actions table.

        It defaults to FALSE because the INDstocks feed - the only equity feed wired up -
        already serves split/bonus-adjusted history. Verified 2026-09-12 against 40 real
        splits/bonuses from NSE's own corporate-action export: 39 showed a perfectly smooth
        series across the ex-date (the 40th was a 0.909 factor, inside noise). Applying our
        own factors on top double-adjusts and silently corrupts history - e.g. NESTLEIND's
        1:10 split (ex 2024-01-05) turned a real Rs 1,361 close into Rs 68.

        Set it True only for a feed that genuinely serves unadjusted prices. The
        corporate_actions table stays populated either way: it still drives the
        `suspected_unadjusted_split` detector and is useful for audit.
        """
        self.path = str(path)
        self.apply_corporate_actions = apply_corporate_actions
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(self.path)
        for stmt in _SCHEMA.strip().split(";"):
            if stmt.strip():
                self.con.execute(stmt)

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> CandleStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------- candles

    def upsert_candles(self, candles: Iterable[Candle]) -> int:
        rows = [
            (c.scrip_code, c.interval.value, _epoch(c.ts), c.open, c.high, c.low, c.close, c.volume)
            for c in candles
        ]
        if not rows:
            return 0
        df = pd.DataFrame(
            rows, columns=["scrip_code", "interval", "ts", *CANDLE_COLUMNS]
        ).drop_duplicates(subset=["scrip_code", "interval", "ts"], keep="last")
        self.con.register("_incoming", df)
        self.con.execute("INSERT OR REPLACE INTO candles SELECT * FROM _incoming")
        self.con.unregister("_incoming")
        return len(df)

    def last_ts(self, scrip_code: str, interval: Interval) -> datetime | None:
        row = self.con.execute(
            "SELECT max(ts) FROM candles WHERE scrip_code = ? AND interval = ?",
            [scrip_code, interval.value],
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return datetime.fromtimestamp(int(row[0]), tz=IST)

    def first_ts(self, scrip_code: str, interval: Interval) -> datetime | None:
        row = self.con.execute(
            "SELECT min(ts) FROM candles WHERE scrip_code = ? AND interval = ?",
            [scrip_code, interval.value],
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return datetime.fromtimestamp(int(row[0]), tz=IST)

    def codes(self, interval: Interval) -> list[str]:
        rows = self.con.execute(
            "SELECT DISTINCT scrip_code FROM candles WHERE interval = ? ORDER BY 1",
            [interval.value],
        ).fetchall()
        return [r[0] for r in rows]

    def count(self, scrip_code: str, interval: Interval) -> int:
        row = self.con.execute(
            "SELECT count(*) FROM candles WHERE scrip_code = ? AND interval = ?",
            [scrip_code, interval.value],
        ).fetchone()
        return int(row[0]) if row else 0

    def load(
        self,
        scrip_code: str,
        interval: Interval,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """OHLCV DataFrame indexed by tz-aware IST open time, ascending. `end` exclusive."""
        sql = (
            "SELECT ts, open, high, low, close, volume FROM candles "
            "WHERE scrip_code = ? AND interval = ?"
        )
        params: list[object] = [scrip_code, interval.value]
        if start is not None:
            sql += " AND ts >= ?"
            params.append(_epoch(start))
        if end is not None:
            sql += " AND ts < ?"
            params.append(_epoch(end))
        sql += " ORDER BY ts"
        df = self.con.execute(sql, params).df()
        idx = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
        df = df.drop(columns=["ts"]).set_index(pd.DatetimeIndex(idx, name="ts"))
        df["volume"] = df["volume"].astype("int64")
        if adjusted and self.apply_corporate_actions and not df.empty:
            symbol = self.symbol_for(scrip_code)
            if symbol:
                df = apply_adjustments(df, self.corporate_actions(symbol))
        return df

    # --------------------------------------------------------------- instruments

    def upsert_instruments(self, instruments: Iterable[Instrument | IndexInstrument]) -> int:
        rows = []
        for i in instruments:
            if isinstance(i, Instrument):
                rows.append(
                    (
                        i.scrip_code, i.exch, i.security_id, "equity", i.trading_symbol,
                        i.symbol_name, i.series, i.instrument_name, i.tick_size, i.lot_units,
                        i.custom_symbol or i.trading_symbol,
                    )
                )  # fmt: skip
            else:
                rows.append(
                    (i.scrip_code, i.exch, i.security_id, "index", i.name, i.name, "", "INDEX",
                     None, None, i.name)
                )  # fmt: skip
        if not rows:
            return 0
        self.con.executemany(
            "INSERT OR REPLACE INTO instruments VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
        )
        return len(rows)

    def symbol_for(self, scrip_code: str) -> str | None:
        row = self.con.execute(
            "SELECT trading_symbol FROM instruments WHERE scrip_code = ?", [scrip_code]
        ).fetchone()
        return row[0] if row else None

    def custom_symbols(self, scrip_codes: Sequence[str]) -> dict[str, str]:
        """scrip_code -> `name` (upsert_instruments stores Instrument.custom_symbol there,
        falling back to trading_symbol) for whichever of `scrip_codes` have one set. For
        CoinDCX instruments this is the CoinDCX `pair` (e.g. "I-BTC_INR") - see
        broker/coindcx/instruments.py - which candles_history() needs to resolve a
        scrip_code like "CDX_BTCINR" back to the identifier the API actually wants."""
        if not scrip_codes:
            return {}
        placeholders = ",".join("?" * len(scrip_codes))
        rows = self.con.execute(
            f"SELECT scrip_code, name FROM instruments "
            f"WHERE scrip_code IN ({placeholders}) AND name != ''",
            list(scrip_codes),
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def scrip_code_for(self, symbol: str, exch: str = "NSE", kind: str = "equity") -> str | None:
        row = self.con.execute(
            "SELECT scrip_code FROM instruments WHERE trading_symbol = ? AND exch = ? AND kind = ?",
            [symbol, exch, kind],
        ).fetchone()
        return row[0] if row else None

    def index_code(self, name: str, exch: str = "NSE") -> str | None:
        return self.scrip_code_for(name, exch=exch, kind="index")

    def instrument_codes(
        self, kind: str = "equity", exch: str = "NSE", series: str | None = "EQ"
    ) -> list[str]:
        sql = "SELECT scrip_code FROM instruments WHERE kind = ? AND exch = ?"
        params: list[object] = [kind, exch]
        if series is not None:
            sql += " AND series = ?"
            params.append(series)
        return [r[0] for r in self.con.execute(sql + " ORDER BY 1", params).fetchall()]

    # --------------------------------------------------------- corporate actions

    def upsert_corporate_actions(self, actions: Iterable[CorporateAction]) -> int:
        rows = [
            (a.symbol, a.ex_date, a.kind.value, a.price_factor.numerator,
             a.price_factor.denominator, a.purpose, a.source)
            for a in actions
        ]  # fmt: skip
        if not rows:
            return 0
        self.con.executemany(
            "INSERT OR REPLACE INTO corporate_actions VALUES (?,?,?,?,?,?,?)", rows
        )
        return len(rows)

    def corporate_actions(self, symbol: str) -> list[CorporateAction]:
        rows = self.con.execute(
            "SELECT symbol, ex_date, kind, factor_num, factor_den, purpose, source "
            "FROM corporate_actions WHERE symbol = ? ORDER BY ex_date",
            [symbol],
        ).fetchall()
        return [
            CorporateAction(
                symbol=r[0], ex_date=r[1], kind=ActionKind(r[2]),
                price_factor=Fraction(int(r[3]), int(r[4])), purpose=r[5] or "", source=r[6] or "",
            )
            for r in rows
        ]  # fmt: skip

    # ------------------------------------------------------------ results dates

    def upsert_results_events(self, events: Iterable[ResultsEvent]) -> int:
        rows = [(e.symbol, e.event_date, e.purpose, e.source) for e in events]
        if not rows:
            return 0
        self.con.executemany("INSERT OR REPLACE INTO results_events VALUES (?,?,?,?)", rows)
        return len(rows)

    def next_results_date(self, symbol: str, after: date) -> date | None:
        row = self.con.execute(
            "SELECT min(event_date) FROM results_events WHERE symbol = ? AND event_date >= ?",
            [symbol, after],
        ).fetchone()
        return row[0] if row and row[0] else None

    def results_within(
        self, symbol: str, start: date, sessions: int, calendar: Sequence[date]
    ) -> date | None:
        """First results date inside the next `sessions` trading days after `start`."""
        future = [d for d in calendar if d > start][:sessions]
        if not future:
            return None
        nxt = self.next_results_date(symbol, start + timedelta(days=1))
        return nxt if nxt is not None and nxt <= future[-1] else None


def apply_adjustments(df: pd.DataFrame, actions: Sequence[CorporateAction]) -> pd.DataFrame:
    """Back-adjust prices (multiply) and volume (divide) for every split/bonus whose
    ex-date is after the candle's open date. Returns a new frame; input untouched."""
    out = df.copy()
    if out.empty:
        return out
    dates = np.array(ist_dates(out))
    for a in actions:
        if not a.adjusts_prices:
            continue
        mask = dates < a.ex_date
        if not mask.any():
            continue
        f = float(a.price_factor)
        for col in ("open", "high", "low", "close"):
            out.loc[mask, col] = out.loc[mask, col] * f
        out.loc[mask, "volume"] = (out.loc[mask, "volume"] / f).round().astype("int64")
    return out
