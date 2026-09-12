"""`tradedesk` command line. M1: `config check`, `costs`. M2: `auth`, `instruments`,
`candles`, `quote`, `stream`. Later milestones' commands are registered so the list in
CLAUDE.md is accurate and fail with the milestone that implements them."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from pydantic import ValidationError

from tradedesk.config import load_config
from tradedesk.models import TradeType
from tradedesk.risk.costs import LegCost, net_reward_risk, round_trip_cost

if TYPE_CHECKING:
    from tradedesk.alerts import AlertRouter
    from tradedesk.broker.indstocks import IndstocksClient
    from tradedesk.data.candle_store import CandleStore

app = typer.Typer(no_args_is_help=True, add_completion=False)
config_app = typer.Typer(no_args_is_help=True)
app.add_typer(config_app, name="config", help="Inspect the YAML files under config/.")
auth_app = typer.Typer(no_args_is_help=True)
app.add_typer(auth_app, name="auth", help="INDstocks credentials (stored in the OS keychain).")
instruments_app = typer.Typer(no_args_is_help=True)
app.add_typer(instruments_app, name="instruments", help="Instrument master files.")
data_app = typer.Typer(no_args_is_help=True)
app.add_typer(data_app, name="data", help="Candle store: load history, import events, quality.")
alerts_app = typer.Typer(no_args_is_help=True)
app.add_typer(alerts_app, name="alerts", help="Alert channels: Telegram setup, test sends.")
journal_app = typer.Typer(no_args_is_help=True)
app.add_typer(journal_app, name="journal", help="Your fills, positions, tags and stats.")
paper_app = typer.Typer(no_args_is_help=True)
app.add_typer(paper_app, name="paper", help="Paper book: every triggered signal, simulated.")

JOURNAL_OPTION = typer.Option(Path("data/journal.sqlite"), "--journal", help="SQLite journal.")

DB_OPTION = typer.Option(Path("data/tradedesk.duckdb"), "--db", help="DuckDB file.")


ROOT_OPTION = typer.Option(Path("."), "--root", help="Project root containing config/.")


MARKET_OPTION = typer.Option(
    "nse",
    "--market",
    help="nse|crypto|bse - crypto uses CoinDCX (M13), no credentials needed; bse uses the "
    "same INDstocks broker/credentials as nse, just a different exchange prefix",
)
CRYPTO_DB = Path("data/crypto.duckdb")
CRYPTO_REFERENCE_CODE = "CDX_BTCINR"  # trades every calendar day; stands in for an index
BSE_DB = Path("data/bse.duckdb")


def _resolve_db(db: Path, market: str) -> Path:
    """`--db` explicitly given wins; otherwise crypto/bse default to their own store so a
    `data load --market crypto|bse` never touches the NSE duckdb file."""
    if db != DB_OPTION.default:
        return db
    if market == "crypto":
        return CRYPTO_DB
    if market == "bse":
        return BSE_DB
    return db


@config_app.command("check")
def config_check(root: Path = ROOT_OPTION) -> None:
    """Load every config file and print a summary; non-zero exit on validation errors."""
    try:
        s = load_config(root)
    except (ValidationError, FileNotFoundError, ValueError) as exc:
        typer.echo(f"CONFIG INVALID\n{exc}", err=True)
        raise typer.Exit(code=1) from None
    r = s.risk
    typer.echo("config OK")
    typer.echo(f"  trading capital      Rs {r.trading_capital:,.0f}")
    typer.echo(f"  risk per trade       {r.max_risk_per_trade_pct:.2%}")
    typer.echo(f"  portfolio heat cap   {r.max_portfolio_heat_pct:.2%}")
    typer.echo(f"  max positions        {r.max_open_positions} (sector cap {r.max_per_sector})")
    typer.echo(f"  min net R:R          {r.min_net_rr}")
    b = r.costs.brokerage
    typer.echo(
        f"  brokerage / order    {b.pct:.2%} of value, Rs {b.min_per_order}-{b.max_per_order}"
    )
    dp = r.costs.dp_charge
    typer.echo(f"  DP charge            Rs {dp.amount} (+GST: {dp.gst_applies})")
    enabled = [k for k, v in s.setups.setups.items() if v.enabled]
    typer.echo(f"  setups enabled       {enabled or 'none'}")
    typer.echo(f"  claude mode          {s.claude.mode}")
    typer.echo(f"  ml enabled/shadow    {s.ml.enabled}/{s.ml.shadow}")


def _print_leg(label: str, leg: LegCost) -> None:
    typer.echo(f"{label}: {leg.side.value} {leg.qty} @ {leg.price}  (turnover {leg.turnover:,.2f})")
    for name in (
        "brokerage", "stt", "exchange_txn", "ipft", "sebi_fee", "stamp_duty", "gst", "dp_charge"
    ):  # fmt: skip
        typer.echo(f"    {name:<13}{getattr(leg, name):>10}")
    typer.echo(f"    {'total':<13}{leg.total:>10}")


@app.command()
def costs(
    trade_type: TradeType = typer.Option(..., "--type", help="intraday or delivery"),
    qty: int = typer.Option(..., min=1),
    entry_arg: str = typer.Option(..., "--entry", help="Entry (buy) price"),
    exit_arg: str = typer.Option(..., "--exit", help="Exit (sell) price"),
    stop_arg: str | None = typer.Option(
        None, "--stop", help="Stop price; enables the net R:R line"
    ),
    root: Path = ROOT_OPTION,
) -> None:
    """Break down the charges on a long round trip using config/risk.yaml rates."""
    # typer has no Decimal type; parse the strings ourselves so prices never touch float.
    entry, exit_price = Decimal(entry_arg), Decimal(exit_arg)
    stop = Decimal(stop_arg) if stop_arg is not None else None
    schedule = load_config(root).risk.costs
    rt = round_trip_cost(
        schedule, trade_type=trade_type, qty=qty, entry_price=entry, exit_price=exit_price
    )
    _print_leg("entry", rt.entry)
    _print_leg("exit ", rt.exit)
    typer.echo(f"round trip: Rs {rt.total}  ({rt.pct_of_entry_value:.3%} of entry value)")
    gross = (exit_price - entry) * qty
    typer.echo(f"gross P&L: Rs {gross}   net P&L: Rs {gross - rt.total}")
    if stop is not None:
        rr = net_reward_risk(
            schedule, trade_type=trade_type, qty=qty, entry=entry, stop=stop, target=exit_price
        )
        gross_rr = (exit_price - entry) / (entry - stop)
        typer.echo(f"reward:risk to {exit_price}: gross {gross_rr:.2f}R, net {rr:.2f}R")


# ------------------------------------------------------------------ M2: INDstocks


def _client() -> IndstocksClient:
    import httpx

    from tradedesk.broker.indstocks import IndstocksClient, TokenProvider
    from tradedesk.broker.indstocks.rest import BASE_URL

    http = httpx.AsyncClient(base_url=BASE_URL, timeout=30.0)
    return IndstocksClient(TokenProvider(http=http))


async def _with_client[R](fn: Callable[[IndstocksClient], Awaitable[R]]) -> R:
    client = _client()
    try:
        return await fn(client)
    finally:
        await client.aclose()


def _stdin_is_windows_console() -> bool:
    """True only when getpass can actually read hidden input on this terminal.

    Python's getpass on Windows reads from the console device via msvcrt, which hangs or
    swallows keystrokes under Git Bash / mintty, VS Code's pseudo-terminals and piped stdin.
    """
    import sys

    if not sys.stdin.isatty():
        return False
    try:
        import ctypes
        import msvcrt  # noqa: F401 - presence means a Windows build

        handle = ctypes.windll.kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint()
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except (ImportError, AttributeError, OSError):
        return True  # POSIX: getpass works on a tty


def _read_secret(label: str, hidden: bool) -> str:
    import sys

    if hidden:
        return str(typer.prompt(label, hide_input=True))
    sys.stderr.write(f"{label}: ")
    sys.stderr.flush()
    line = sys.stdin.readline()
    if not line:
        raise typer.BadParameter(f"no input for {label}")
    return line.rstrip("\r\n")


def _normalise_totp_secret(secret: str) -> str:
    import base64
    import binascii

    cleaned = secret.replace(" ", "").replace("-", "").strip().upper()
    try:
        base64.b32decode(cleaned + "=" * (-len(cleaned) % 8), casefold=True)
    except (binascii.Error, ValueError):
        raise typer.BadParameter("TOTP secret must be base32 (A-Z, 2-7)") from None
    return cleaned


@auth_app.command("setup")
def auth_setup(
    from_stdin: bool = typer.Option(
        False,
        "--stdin",
        help="Read three lines from stdin (Client ID, MPIN, TOTP secret) instead of prompting.",
    ),
) -> None:
    """Store Client ID, MPIN and TOTP secret in the OS keychain.

    Hidden input is used when the terminal supports it (Windows Terminal / cmd / PowerShell
    console). Under Git Bash, mintty or a pseudo-terminal the values are read as plain lines
    and WILL be echoed - clear the terminal afterwards, or pipe them in with --stdin.
    """
    import sys

    from tradedesk.broker.indstocks.auth import (
        KEY_CLIENT_ID,
        KEY_MPIN,
        KEY_TOTP_SECRET,
        KeyringStore,
    )

    if from_stdin:
        lines = [ln.rstrip("\r\n") for ln in sys.stdin.read().splitlines()]
        lines = [ln for ln in lines if ln.strip()]
        if len(lines) != 3:
            raise typer.BadParameter("--stdin expects exactly 3 non-empty lines")
        client_id, mpin, secret = lines
    else:
        hidden = _stdin_is_windows_console()
        typer.echo("From indstocks.com/app/api-trading/access-tokens (after 'Setup TOTP'):")
        if not hidden:
            typer.echo(
                "NOTE: this terminal cannot hide input; values will be visible as you type. "
                "Clear the screen afterwards, or use `tradedesk auth setup --stdin < file`."
            )
        client_id = _read_secret("Client ID (x-api-key)", hidden=False)
        mpin = _read_secret("MPIN", hidden=hidden)
        secret = _read_secret("TOTP secret (base32 key shown once)", hidden=hidden)

    client_id = client_id.strip()
    mpin = mpin.strip()
    if not client_id or not mpin:
        raise typer.BadParameter("Client ID and MPIN must not be empty")
    secret = _normalise_totp_secret(secret)

    store = KeyringStore()
    store.set(KEY_CLIENT_ID, client_id)
    store.set(KEY_MPIN, mpin)
    store.set(KEY_TOTP_SECRET, secret)
    typer.echo("stored in keychain service 'tradedesk-indstocks'. Now run: tradedesk auth check")


@auth_app.command("status")
def auth_status() -> None:
    """Show which credentials are stored (never the values)."""
    from tradedesk.broker.indstocks.auth import (
        KEY_CLIENT_ID,
        KEY_MPIN,
        KEY_TOTP_SECRET,
        KeyringStore,
    )

    store = KeyringStore()
    for k in (KEY_CLIENT_ID, KEY_MPIN, KEY_TOTP_SECRET):
        v = store.get(k)
        typer.echo(f"  {k:<12} {'set (' + str(len(v)) + ' chars)' if v else 'MISSING'}")


@auth_app.command("clear")
def auth_clear() -> None:
    """Remove the stored INDstocks credentials from the keychain."""
    from tradedesk.broker.indstocks.auth import (
        KEY_CLIENT_ID,
        KEY_MPIN,
        KEY_TOKEN,
        KEY_TOKEN_ISSUED_AT,
        KEY_TOKEN_LAST_ATTEMPT,
        KEY_TOTP_SECRET,
        KeyringStore,
    )

    store = KeyringStore()
    for k in (
        KEY_CLIENT_ID,
        KEY_MPIN,
        KEY_TOTP_SECRET,
        KEY_TOKEN,
        KEY_TOKEN_ISSUED_AT,
        KEY_TOKEN_LAST_ATTEMPT,
    ):
        store.delete(k)
    typer.echo("cleared")


@auth_app.command("check")
def auth_check() -> None:
    """Generate a token via TOTP and call /user/profile to prove it works."""

    async def go(c: IndstocksClient) -> None:
        profile = await c.profile()
        token = c.tokens.token or ""
        typer.echo(f"token OK ({token[:6]}...{token[-4:]}, {len(token)} chars)")
        typer.echo(f"user {profile.user_id} ucc={profile.ucc} nse={profile.is_nse_onboarded}")
        funds = await c.funds()
        typer.echo(
            f"withdrawable Rs {funds.withdrawal_balance:,.2f}; "
            f"today's charges Rs {funds.eq_charges}"
        )

    asyncio.run(_with_client(go))


@instruments_app.command("refresh")
def instruments_refresh(
    out: Path = typer.Option(Path("data/instruments"), "--out"),
) -> None:
    """Download equity, fno and index instrument masters to CSV files."""

    async def go(c: IndstocksClient) -> None:
        from tradedesk.broker.indstocks.instruments import (
            nse_cash_equities,
            parse_index_csv,
            parse_instruments_csv,
        )

        out.mkdir(parents=True, exist_ok=True)
        for source in ("equity", "fno", "index"):
            text = await c.instruments_csv(source)
            path = out / f"{source}.csv"
            path.write_text(text, encoding="utf-8")
            if source == "index":
                n = len(parse_index_csv(text))
            else:
                rows = parse_instruments_csv(text)
                n = len(rows)
                if source == "equity":
                    typer.echo(f"  NSE cash EQ series: {len(nse_cash_equities(rows))}")
            typer.echo(f"{path}: {n} rows")

    asyncio.run(_with_client(go))


@app.command()
def candles(
    scrip_code: str = typer.Argument(..., help="e.g. NSE_3045"),
    interval: str = typer.Option("1day", "--interval"),
    days: int = typer.Option(30, "--days", min=1),
    tail: int = typer.Option(10, "--tail", min=1),
) -> None:
    """Print the last N candles (paged history) - compare closes with NSE's official close."""
    from tradedesk.broker.indstocks.models import IST, Interval

    iv = Interval(interval)
    end = datetime.now(IST)
    start = end - timedelta(days=days)

    async def go(c: IndstocksClient) -> None:
        got = await c.candles_history(iv, [scrip_code], start, end)
        rows = got.get(scrip_code, [])
        typer.echo(
            f"{scrip_code} {iv.value}: {len(rows)} candles from {start:%Y-%m-%d} to {end:%Y-%m-%d}"
        )
        hdr = (
            f"{'open time (IST)':<20}{'open':>10}{'high':>10}{'low':>10}{'close':>10}{'volume':>12}"
        )
        typer.echo(hdr)
        for k in rows[-tail:]:
            typer.echo(
                f"{k.ts:%Y-%m-%d %H:%M}    {k.open:>10.2f}{k.high:>10.2f}"
                f"{k.low:>10.2f}{k.close:>10.2f}{k.volume:>12,}"
            )

    asyncio.run(_with_client(go))


@app.command()
def quote(scrip_codes: list[str] = typer.Argument(..., help="e.g. NSE_3045 NSE_2885")) -> None:
    """Full quote snapshot: LTP, OHLC, circuits, volume."""

    async def go(c: IndstocksClient) -> None:
        got = await c.quotes_full(scrip_codes)
        for code in scrip_codes:
            q = got.get(code)
            if q is None:
                typer.echo(f"{code}: no data")
                continue
            typer.echo(
                f"{code}: ltp {q.live_price} o/h/l {q.day_open}/{q.day_high}/{q.day_low} "
                f"prev {q.prev_close} vol {q.volume} circuit {q.lower_circuit}-{q.upper_circuit}"
            )

    asyncio.run(_with_client(go))


@app.command()
def stream(
    instruments: list[str] = typer.Argument(..., help="e.g. NSE:3045 NSE:2885"),
    seconds: int = typer.Option(30, "--seconds", min=1),
    mode: str = typer.Option("ltp", "--mode"),
) -> None:
    """Subscribe to the price WebSocket and print ticks for a while."""
    from tradedesk.broker.indstocks import PriceFeed, Tick

    async def go(c: IndstocksClient) -> None:
        count = 0

        def on_tick(t: Tick) -> None:
            nonlocal count
            count += 1
            typer.echo(f"{t.timestamp:%H:%M:%S.%f} {t.instrument:<8} {t.mode} {t.data}")

        feed = PriceFeed(c.tokens, on_tick, mode=mode)
        await feed.subscribe(instruments)
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(stop))
        await asyncio.sleep(seconds)
        stop.set()
        await task
        typer.echo(f"{count} ticks in {seconds}s; reconnects={feed.reconnects}")

    asyncio.run(_with_client(go))


# ------------------------------------------------------------------- M3: data


def _store(db: Path) -> CandleStore:
    from tradedesk.data.candle_store import CandleStore

    return CandleStore(db)


def _reference_code(
    store: CandleStore, root: Path, benchmark: str | None = None, exch: str = "NSE"
) -> str:
    """`benchmark`/`exch` override config/universe.yaml's NSE defaults - pass a market's
    `benchmark_name` and code_prefix's exchange (e.g. bse_market(settings).benchmark_name
    == "SENSEX", exch="BSE") for a market whose reference index needs the same
    instruments-table name lookup NSE's does (unlike crypto, whose scrip codes are
    deterministic slugs, not opaque numeric ids). `CandleStore.index_code` defaults to
    exch="NSE" same as `instrument_codes` - passing the wrong exchange here silently
    returns "not found" instead of an error, the exact bug shape CLAUDE.md's 2026-09-12
    health check found for the VIX lookup, so this is deliberately an explicit parameter."""
    name = benchmark or load_config(root).universe.benchmark
    code = store.index_code(name, exch=exch)
    if code is None:
        raise typer.BadParameter(
            f"benchmark {name!r} not in instruments table; run `tradedesk data sync-instruments`"
        )
    return code


def _sector_config(store: CandleStore, root: Path) -> tuple[dict[str, str], dict[str, str]]:
    """NSE-only sector context for prediction/train.py::market_context()'s
    sector_return_1d/5d (Phase 2, 2026-09-13): reads config/sector_membership.yaml
    (symbol -> sector index name, curated from real NSE constituent lists - only BANK
    NIFTY/Nifty Financial are populated, see that file's header comment for why the other
    7 originally-planned sectors were dropped) and resolves it into the two dicts
    BacktestConfig needs - `sector_of` (stock scrip_code -> sector name, also reused by
    backtest/portfolio.py's sector-concentration cap, which was silently dead before this
    since nothing ever populated it) and `sector_codes` (sector name -> that index's OWN
    scrip code, so prepare_market() knows which candles to load). Missing/unresolvable
    entries are silently skipped - same graceful-degradation posture as vix_code."""
    import yaml

    path = root / "config" / "sector_membership.yaml"
    if not path.exists():
        return {}, {}
    membership: dict[str, list[str]] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sector_of: dict[str, str] = {}
    sector_codes: dict[str, str] = {}
    for sector_name, symbols in membership.items():
        sector_code = store.index_code(sector_name)
        if sector_code is None:
            continue
        sector_codes[sector_name] = sector_code
        for symbol in symbols:
            code = store.scrip_code_for(symbol, exch="NSE", kind="equity")
            if code is not None:
                sector_of[code] = sector_name
    return sector_of, sector_codes


@data_app.command("sync-instruments")
def data_sync_instruments(db: Path = DB_OPTION, market: str = MARKET_OPTION) -> None:
    """Download the instrument master into the store's instruments table."""
    db = _resolve_db(db, market)

    if market == "crypto":
        from tradedesk.broker.coindcx import CoinDcxClient

        async def go_crypto() -> None:
            async with CoinDcxClient() as c:
                instruments = await c.instruments()
                with _store(db) as store:
                    n = store.upsert_instruments(instruments)
                typer.echo(f"{n} active INR pairs stored in {db}")

        asyncio.run(go_crypto())
        return

    async def go(c: IndstocksClient) -> None:
        from tradedesk.broker.indstocks.instruments import bse_cash_equities, nse_cash_equities

        eq = await c.equity_instruments()
        idx = await c.index_instruments()
        cash = bse_cash_equities(eq) if market == "bse" else nse_cash_equities(eq)
        with _store(db) as store:
            n = store.upsert_instruments([*cash, *idx])
        typer.echo(f"{n} instruments stored ({len(idx)} indices) in {db}")

    asyncio.run(_with_client(go))


@data_app.command("load")
def data_load(
    codes: list[str] = typer.Argument(None, help="Scrip codes; default = universe + benchmark"),
    interval: str = typer.Option("1day", "--interval"),
    years: float | None = typer.Option(
        None, "--years", help="History depth; default 10 daily / 2 intraday"
    ),
    db: Path = DB_OPTION,
    root: Path = ROOT_OPTION,
    market: str = MARKET_OPTION,
) -> None:
    """Pull candle history into the store (incremental: only new bars are fetched)."""
    from tradedesk.broker.indstocks.models import IST, Interval
    from tradedesk.data.history_loader import LoadResult, default_start, load_history

    db = _resolve_db(db, market)
    iv = Interval(interval)
    now = datetime.now(IST)
    start = now - timedelta(days=365 * years) if years else default_start(iv, now)

    def make_progress(targets: list[str]) -> Callable[[LoadResult], None]:
        done = 0

        def progress(r: LoadResult) -> None:
            nonlocal done
            done += 1
            if done % 25 == 0 or r.error:
                typer.echo(
                    f"  {done}/{len(targets)} {r.scrip_code}: {r.fetched} bars {r.error or ''}"
                )

        return progress  # fmt: skip

    if market == "crypto":
        from tradedesk.broker.coindcx import CoinDcxClient
        from tradedesk.broker.coindcx.rest import CoinDcxError

        async def go_crypto() -> None:
            async with CoinDcxClient() as c:
                with _store(db) as store:
                    targets = list(codes) if codes else store.instrument_codes(
                        kind="equity", exch="CDX", series="INR"
                    )  # fmt: skip
                    if not targets:
                        raise typer.BadParameter(
                            "no codes: pass scrip codes or run "
                            "data sync-instruments --market crypto"
                        )
                    # candles_history() takes scrip codes, but CoinDCX's own API wants
                    # its `pair` identifier ("I-BTC_INR", not "CDX_BTCINR") - the
                    # instruments table already has that mapping from sync-instruments,
                    # no extra API call needed. A code with none registered (never
                    # synced, or delisted since) just fetches nothing for that code.
                    c.register_pairs(store.custom_symbols(targets))
                    typer.echo(
                        f"loading {iv.value} for {len(targets)} pairs "
                        f"from {start:%Y-%m-%d} into {db}"
                    )
                    summary = await load_history(
                        c, store, targets, iv, start=start, end=now,
                        max_codes_per_call=1, error_types=(CoinDcxError,),
                        progress=make_progress(targets),
                    )  # fmt: skip
                    typer.echo(f"fetched {summary.fetched} bars; {len(summary.errors)} errors")
                    for r in summary.errors[:20]:
                        typer.echo(f"  ERROR {r.scrip_code}: {r.error}")

        asyncio.run(go_crypto())
        return

    async def go(c: IndstocksClient) -> None:
        with _store(db) as store:
            # instrument_codes() defaults to exch="NSE" - the BSE store only ever has
            # exch="BSE" rows in it (data_sync_instruments filters at write time), so the
            # default would silently return zero targets for `--market bse`.
            # instrument_codes()'s series="EQ" default is NSE's single equity series - BSE
            # has no such series (it's filtered to A/B groups at sync time instead, see
            # bse_cash_equities), so the filter must be dropped, not just re-pointed.
            exch = "BSE" if market == "bse" else "NSE"
            series = None if market == "bse" else "EQ"
            targets = list(codes) if codes else store.instrument_codes(
                kind="equity", exch=exch, series=series
            )  # fmt: skip
            settings = load_config(root)
            benchmark_name = settings.bse_market.benchmark if market == "bse" else settings.universe.benchmark  # noqa: E501
            vix_name = None if market == "bse" else settings.universe.volatility_index
            ref = store.index_code(benchmark_name, exch=exch)
            if ref and ref not in targets:
                targets.append(ref)
            # The volatility index was never added here, so India VIX sat in the
            # instruments table with zero candles and engine/regime.py always saw
            # vix=None - it degrades gracefully, which is exactly why nothing ever
            # surfaced the gap. The regime has been running on benchmark+breadth only.
            # BSE has no volatility_index configured yet (see BseMarketConfig) - fetching
            # one without also flipping vix_required would just be dead weight.
            vix_code = store.index_code(vix_name) if vix_name else None
            if vix_code and vix_code not in targets:
                targets.append(vix_code)
            # Sector indices (config/universe.yaml::sector_indices) feed
            # prediction/train.py::market_context()'s sector_return_1d/5d features - same
            # "load the benchmark alongside the universe" pattern as VIX above. NSE only;
            # BSE has no sector_indices configured (BseMarketConfig has no such field).
            if market != "bse":
                for sector_name in settings.universe.sector_indices:
                    sector_code = store.index_code(sector_name)
                    if sector_code is None:
                        typer.echo(f"  WARNING sector index not found in instruments: {sector_name}")  # noqa: E501
                    elif sector_code not in targets:
                        targets.append(sector_code)
            if not targets:
                raise typer.BadParameter("no codes: pass scrip codes or run data sync-instruments")
            typer.echo(
                f"loading {iv.value} for {len(targets)} codes from {start:%Y-%m-%d} into {db}"
            )
            summary = await load_history(
                c, store, targets, iv, start=start, end=now, progress=make_progress(targets)
            )
            typer.echo(f"fetched {summary.fetched} bars; {len(summary.errors)} errors")
            for r in summary.errors[:20]:
                typer.echo(f"  ERROR {r.scrip_code}: {r.error}")

            # Straggler sweep. A pass can leave codes behind the newest bar while
            # reporting fetched=0 and NO error - the loader cannot tell "no new bar
            # exists" from "the API returned nothing for this code". A real run on
            # 2026-09-12 left 739 of 2640 codes (and NIFTY, the benchmark) a session
            # back this way; re-running the identical command fetched them all with
            # zero errors, which is what makes it a fetch gap and not missing data.
            # One retry of just the laggards is cheap and closes it.
            def behind_newest() -> tuple[list[str], datetime | None]:
                seen = {x: t for x in targets if (t := store.last_ts(x, iv)) is not None}
                if not seen:
                    return [], None
                newest = max(seen.values())
                return sorted(x for x, t in seen.items() if t < newest), newest

            behind, newest = behind_newest()
            if behind and newest is not None:
                typer.echo(f"{len(behind)} codes still behind {newest:%Y-%m-%d}; retrying them")
                retry = await load_history(
                    c, store, behind, iv, start=start, end=now, progress=make_progress(behind)
                )
                still, _ = behind_newest()
                typer.echo(
                    f"  retry fetched {retry.fetched} bars; {len(still)} still behind"
                    + (f" ({', '.join(still[:5])})" if still else "")
                )
            from tradedesk.broker.indstocks.ratelimit import Category

            typer.echo(f"data-API calls today: {c.limiter.used_today(Category.DATA)}")

    asyncio.run(_with_client(go))


@data_app.command("import-actions")
def data_import_actions(
    csv_file: Path = typer.Argument(..., exists=True), db: Path = DB_OPTION
) -> None:
    """Import NSE's corporate-actions CSV (splits and bonuses adjust prices on read)."""
    from tradedesk.data.corporate_actions import parse_nse_corporate_actions_csv

    actions = parse_nse_corporate_actions_csv(csv_file.read_text(encoding="utf-8-sig"))
    with _store(db) as store:
        n = store.upsert_corporate_actions(actions)
    adjusting = sum(1 for a in actions if a.adjusts_prices)
    unparsed = [a for a in actions if a.kind.value != "other" and not a.adjusts_prices]
    typer.echo(
        f"{n} actions stored; {adjusting} adjust prices; "
        f"{len(unparsed)} split/bonus rows with unparsed ratio"
    )
    for a in unparsed[:10]:
        typer.echo(f"  CHECK {a.symbol} {a.ex_date}: {a.purpose}")


@data_app.command("import-results")
def data_import_results(
    csv_file: Path = typer.Argument(..., exists=True), db: Path = DB_OPTION
) -> None:
    """Import NSE's board-meetings CSV (results dates drive the results blackout)."""
    from tradedesk.data.results_calendar import parse_nse_board_meetings_csv

    events = parse_nse_board_meetings_csv(csv_file.read_text(encoding="utf-8-sig"))
    with _store(db) as store:
        n = store.upsert_results_events(events)
    typer.echo(f"{n} results events stored")


@data_app.command("quality")
def data_quality(
    codes: list[str] = typer.Argument(None),
    out: Path | None = typer.Option(None, "--out", help="Write the full issue list as CSV"),
    db: Path = DB_OPTION,
    root: Path = ROOT_OPTION,
    market: str = MARKET_OPTION,
) -> None:
    """Data-quality report: gaps, bad bars, big jumps, suspected unadjusted splits, staleness."""
    from tradedesk.broker.indstocks.models import Interval
    from tradedesk.data.health import run_quality_report

    db = _resolve_db(db, market)
    with _store(db) as store:
        # Crypto trades every calendar day, so a liquid pair stands in for an index -
        # there's no synthetic "crypto benchmark" instrument to resolve via config.
        if market == "crypto":
            ref = CRYPTO_REFERENCE_CODE
        elif market == "bse":
            from tradedesk.markets import bse_market

            mkt = bse_market(load_config(root))
            ref = _reference_code(store, root, mkt.benchmark_name, exch="BSE")
        else:
            ref = _reference_code(store, root)
        targets = list(codes) if codes else [c for c in store.codes(Interval.D1) if c != ref]
        rep = run_quality_report(store, targets, ref)
    typer.echo(f"{rep.codes_checked} codes checked; {len(rep.issues)} issues")
    for kind, n in sorted(rep.by_kind().items(), key=lambda kv: -kv[1]):
        typer.echo(f"  {kind.value:<28}{n:>8}")
    worst = sorted(rep.issues, key=lambda i: -i.count)[:15]
    for i in worst:
        typer.echo(f"  {i.scrip_code:<12}{i.kind.value:<28}{str(i.on or ''):<12}{i.detail}")
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        rep.to_frame().to_csv(out, index=False)
        typer.echo(f"written {out}")


@data_app.command("universe")
def data_universe(
    on: str | None = typer.Option(None, "--on", help="YYYY-MM-DD; default today"),
    db: Path = DB_OPTION,
    root: Path = ROOT_OPTION,
    market: str = MARKET_OPTION,
) -> None:
    """List the liquid universe as of a date, from the rules in config/universe.yaml."""
    from tradedesk.broker.indstocks.models import IST
    from tradedesk.data.universe import UniverseRules, universe_on

    db = _resolve_db(db, market)
    candidates: tuple[str, str | None]
    if market == "crypto":
        from tradedesk.markets import crypto_market

        mkt = crypto_market(load_config(root))
        rules = mkt.universe_rules
        candidates = "CDX", "INR"
    elif market == "bse":
        from tradedesk.markets import bse_market

        mkt = bse_market(load_config(root))
        rules = mkt.universe_rules
        candidates = "BSE", None  # bse_cash_equities() already filtered to series A/B at sync time
    else:
        cfg = load_config(root).universe
        rules = UniverseRules(
            min_avg_turnover_inr=float(cfg.min_avg_daily_turnover_inr),
            min_price=float(cfg.min_price),
        )
        candidates = "NSE", "EQ"
    day = datetime.strptime(on, "%Y-%m-%d").date() if on else datetime.now(IST).date()
    with _store(db) as store:
        codes = store.instrument_codes(kind="equity", exch=candidates[0], series=candidates[1])
        members = universe_on(store, codes, day, rules)
        names = [f"{c} {store.symbol_for(c) or ''}" for c in members]
    typer.echo(f"{len(members)} members on {day}")
    for n in names:
        typer.echo(f"  {n}")


@data_app.command("status")
def data_status(db: Path = DB_OPTION, market: str = MARKET_OPTION) -> None:
    """What the store holds."""
    from tradedesk.broker.indstocks.models import Interval

    db = _resolve_db(db, market)
    with _store(db) as store:
        for iv in (Interval.D1, Interval.H1, Interval.M15):
            codes = store.codes(iv)
            if not codes:
                continue
            by_code = {c: t for c in codes if (t := store.last_ts(c, iv)) is not None}
            stamps = list(by_code.values())
            when = f"{max(stamps):%Y-%m-%d %H:%M}" if stamps else "-"
            typer.echo(f"  {iv.value:<10}{len(codes):>5} codes, latest bar {when}")
            # A silent partial load leaves codes behind the newest bar with no error
            # reported anywhere (a real 2026-09-12 run left 739 of 2640 a session back).
            # Reporting only `max(stamps)` hid that completely, so surface it here.
            if stamps:
                newest = max(stamps)
                behind = sorted(c for c, t in by_code.items() if t < newest)
                if behind:
                    typer.echo(
                        f"  {'':<10}{len(behind):>5} of them BEHIND that bar"
                        f" (e.g. {', '.join(behind[:3])}) - re-run `data load`"
                    )
        n_inst = store.con.execute("SELECT count(*) FROM instruments").fetchone()
        n_ca = store.con.execute("SELECT count(*) FROM corporate_actions").fetchone()
        n_re = store.con.execute("SELECT count(*) FROM results_events").fetchone()
        typer.echo(
            f"  instruments {n_inst[0] if n_inst else 0}, "
            f"corporate actions {n_ca[0] if n_ca else 0}, "
            f"results events {n_re[0] if n_re else 0}"
        )


# ------------------------------------------------------------------- M8: alerts


def _build_router(settings: Any, watchlist: Any, dashboard: Any) -> AlertRouter:
    from tradedesk.alerts import AlertRouter, DesktopNotifier, TelegramBot, load_bot_token

    cfg = settings.alerts
    desktop = DesktopNotifier(sound=cfg.desktop.sound) if cfg.desktop.enabled else None
    telegram = None
    if cfg.telegram.enabled:
        token = load_bot_token()
        if token and cfg.telegram.allowed_chat_id:
            telegram = TelegramBot(token=token, chat_id=int(cfg.telegram.allowed_chat_id))
        else:
            typer.echo(
                "telegram enabled but no bot token / chat id: "
                "run `tradedesk alerts setup-telegram`",
                err=True,
            )
    return AlertRouter(
        cfg, watchlist=watchlist, desktop=desktop, telegram=telegram, dashboard=dashboard
    )


@alerts_app.command("setup-telegram")
def alerts_setup_telegram() -> None:
    """Store the bot token (from @BotFather) in the keychain, then print your chat id."""
    from tradedesk.alerts import TelegramBot, store_bot_token

    hidden = _stdin_is_windows_console()
    token = _read_secret("Bot token from @BotFather", hidden=hidden).strip()
    if ":" not in token:
        raise typer.BadParameter("that does not look like a bot token (expected 123456:ABC...)")
    store_bot_token(token)
    typer.echo("token stored in keychain service 'tradedesk-telegram'.")
    typer.echo("Now send any message to your bot in Telegram, then press Enter.")
    input()
    bot = TelegramBot(token=token, chat_id=0)
    ids = asyncio.run(bot.my_chat_ids())
    if not ids:
        typer.echo(
            "no messages seen yet; message the bot and run `tradedesk alerts telegram-chat-id`"
        )
    else:
        typer.echo(f"chat id(s) that messaged the bot: {ids}")
        typer.echo(
            "put yours in config/alerts.yaml under telegram.allowed_chat_id and set enabled: true"
        )


@alerts_app.command("telegram-chat-id")
def alerts_chat_id() -> None:
    """Print the chat ids that have messaged the bot."""
    from tradedesk.alerts import TelegramBot, load_bot_token

    token = load_bot_token()
    if not token:
        raise typer.BadParameter("no bot token stored; run `tradedesk alerts setup-telegram`")
    typer.echo(asyncio.run(TelegramBot(token=token, chat_id=0).my_chat_ids()))


@alerts_app.command("test")
def alerts_test(root: Path = ROOT_OPTION) -> None:
    """Send a test alert through every enabled channel."""
    from tradedesk.broker.indstocks.models import IST
    from tradedesk.live.models import Alert, AlertKind, AlertLevel

    settings = load_config(root)
    router = _build_router(settings, None, None)
    alert = Alert(
        kind=AlertKind.INFO,
        level=AlertLevel.URGENT,
        at=datetime.now(IST),
        message="tradedesk test alert: desktop + Telegram channels are wired",
    )
    channels = router.route(alert)
    sent = asyncio.run(router.flush())
    typer.echo(
        f"routed to {channels or 'nothing (enable channels in config/alerts.yaml)'}; "
        f"telegram sent {sent}"
    )


@app.command()
def dashboard(
    watchlist: Path | None = typer.Option(None, "--watchlist"),
    session: Path | None = typer.Option(
        None, "--session", help="Recorded session JSONL; default: today's in data/sessions"
    ),
    journal: Path = JOURNAL_OPTION,
    refresh_seconds: int = typer.Option(
        60,
        "--refresh-seconds",
        help="Re-scan for the newest watchlist/session and reload if changed; 0 disables "
        "(pinning both --watchlist and --session also disables, since there is nothing "
        "left to discover)",
    ),
    root: Path = ROOT_OPTION,
) -> None:
    """Serve the localhost dashboard on its own (the live session can also embed it).

    Standalone use - checking the morning's plan, or the day's trigger states, without a
    `tradedesk live` session running right now. Loads the newest watchlist and, if a
    session recording for that date exists, replays it (same engine as `tradedesk replay`,
    no alerts sent) purely to populate the signal states - opening this after `tradedesk
    live` has run today shows what actually happened, not just what was planned. Meant to
    be left running all day (e.g. as its own scheduled task) rather than started fresh
    each time: by default it re-checks for a newer watchlist/session every 60s so a call
    that triggers mid-session shows up without restarting the process.
    """
    from tradedesk.dashboard import DashboardState, create_app, serve
    from tradedesk.live.models import SessionRules
    from tradedesk.live.session import signals_from_watchlist
    from tradedesk.live.trigger_monitor import TriggerMonitor
    from tradedesk.replay import read_session
    from tradedesk.replay import replay as run_replay
    from tradedesk.scan import load_watchlist

    settings = load_config(root)
    state = DashboardState()
    auto_discover = watchlist is None and session is None
    last_loaded: tuple[float | None, float | None] = (None, None)

    def _resolve() -> tuple[Path | None, Path | None]:
        wl_path = watchlist
        if wl_path is None:
            found = sorted(Path("data/watchlists").glob("*.json"))
            wl_path = found[-1] if found else None
        sess_path = session
        if sess_path is None and wl_path is not None:
            candidate = Path("data/sessions") / f"{load_watchlist(wl_path).on.isoformat()}.jsonl"
            sess_path = candidate if candidate.exists() else None
        return wl_path, sess_path

    def _load_once() -> None:
        nonlocal last_loaded
        wl_path, sess_path = _resolve()
        wl_mtime = wl_path.stat().st_mtime if wl_path and wl_path.exists() else None
        sess_mtime = sess_path.stat().st_mtime if sess_path and sess_path.exists() else None
        if (wl_mtime, sess_mtime) == last_loaded and last_loaded != (None, None):
            return
        last_loaded = (wl_mtime, sess_mtime)
        if wl_path is None:
            return
        wl = load_watchlist(wl_path)
        state.set_watchlist(wl)
        typer.echo(f"loaded {wl_path}")
        if sess_path is not None:
            entry_rules = settings.setups.entry
            rules = SessionRules(
                no_entry_before=datetime.strptime(settings.risk.no_entry_window.end, "%H:%M").time(),  # noqa: E501
                late_trigger_after=datetime.strptime(entry_rules.late_trigger_after, "%H:%M").time(),  # noqa: E501
                bar_minutes=entry_rules.confirm_timeframe_minutes,
            )  # fmt: skip
            signals = signals_from_watchlist(wl, alertable_only=False)
            monitor = TriggerMonitor(
                rules,
                signals=signals,
                atr_by_code={t.signal.scrip_code: t.signal.atr for t in signals},
                on_alert=lambda a: None,
            )
            run_replay(read_session(sess_path), monitor)
            state.set_signals(monitor.signals)
            typer.echo(f"replayed {sess_path} ({len(monitor.signals)} signals)")

    _load_once()
    host, port = settings.alerts.dashboard.host, settings.alerts.dashboard.port
    typer.echo(f"dashboard at http://{host}:{port}  (Ctrl+C to stop)")

    async def _run() -> None:
        async def _refresher() -> None:
            while True:
                await asyncio.sleep(refresh_seconds)
                _load_once()

        tasks = []
        if auto_discover and refresh_seconds > 0:
            tasks.append(asyncio.create_task(_refresher()))
        try:
            await serve(create_app(state, journal_path=journal), host=host, port=port)
        finally:
            for t in tasks:
                t.cancel()

    asyncio.run(_run())


# ------------------------------------------------------- M9: journal + paper book


def _calendar(store: CandleStore, root: Path, back_days: int = 400) -> list[date]:
    from tradedesk.broker.indstocks.models import IST
    from tradedesk.data.universe import trading_days

    ref = _reference_code(store, root)
    today = datetime.now(IST).date()
    return trading_days(store, ref, today - timedelta(days=back_days), today)


@journal_app.command("fill")
def journal_fill(
    signal_id: str = typer.Argument(..., help="Signal id from the watchlist / alert"),
    qty: int = typer.Option(..., "--qty", min=1),
    price: float = typer.Option(..., "--price"),
    on: str | None = typer.Option(None, "--date", help="YYYY-MM-DD; default today"),
    journal: Path = JOURNAL_OPTION,
) -> None:
    """Record your real entry fill for a triggered signal (TAKEN -> OPEN)."""
    from tradedesk.backtest.fills import Fill, FillReason, Position
    from tradedesk.broker.indstocks.models import IST
    from tradedesk.engine.lifecycle import SignalState
    from tradedesk.journal import Journal

    day = datetime.strptime(on, "%Y-%m-%d").date() if on else datetime.now(IST).date()
    with Journal(journal) as jn:
        ts = jn.load_signal(signal_id)
        if ts is None:
            raise typer.BadParameter(f"unknown signal {signal_id}")
        if ts.state is SignalState.TRIGGERED:
            ts.move(SignalState.TAKEN, day, f"fill {qty} @ {price}")
        if ts.state is SignalState.TAKEN:
            ts.move(SignalState.OPEN, day)
        elif ts.state is not SignalState.OPEN:
            raise typer.BadParameter(
                f"signal is {ts.state.value}; only triggered signals can be filled"
            )
        pos = Position(
            signal=ts.signal, entry_date=day, entry_price=price, qty_initial=qty, qty_open=qty,
            stop=ts.signal.stop, highest_close=price,
            fills=[Fill(on=day, price=price, qty=qty, reason=FillReason.ENTRY)],
        )  # fmt: skip
        jn.upsert_signal(ts)
        jn.save_position(pos, source="live")
    typer.echo(f"open: {ts.signal.symbol} {qty} @ {price}, stop {ts.signal.stop}")


@journal_app.command("exit")
def journal_exit(
    signal_id: str = typer.Argument(...),
    qty: int = typer.Option(..., "--qty", min=1),
    price: float = typer.Option(..., "--price"),
    reason: str = typer.Option(
        "stop", "--reason", help="stop|gap_stop|partial|trail|time_stop|max_hold|end"
    ),
    on: str | None = typer.Option(None, "--date"),
    journal: Path = JOURNAL_OPTION,
    root: Path = ROOT_OPTION,
) -> None:
    """Record a real exit fill (partial or full). A full exit books the trade with costs."""
    from tradedesk.backtest.fills import Fill, FillReason
    from tradedesk.backtest.portfolio import Portfolio
    from tradedesk.broker.indstocks.models import IST
    from tradedesk.engine.lifecycle import SignalState
    from tradedesk.journal import Journal

    settings = load_config(root)
    day = datetime.strptime(on, "%Y-%m-%d").date() if on else datetime.now(IST).date()
    with Journal(journal) as jn:
        pos = next((p for p in jn.open_positions(source="live") if p.signal.id == signal_id), None)
        if pos is None:
            raise typer.BadParameter(f"no open live position for {signal_id}")
        fill_qty = min(float(qty), pos.qty_open)
        pos.fills.append(Fill(on=day, price=price, qty=fill_qty, reason=FillReason(reason)))
        pos.qty_open -= fill_qty
        if reason == "partial":
            pos.partial_done = True
            old = pos.stop
            pos.stop = max(pos.stop, pos.entry_price)
            jn.record_stop_update(signal_id, day, old, pos.stop, "partial -> breakeven")
        if pos.closed:
            from tradedesk.markets import EquityCostModel

            pf = Portfolio(
                risk=settings.risk, costs=EquityCostModel(settings.risk.costs), equity=1.0
            )
            trade = pf.settle(pos)
            jn.record_trade(trade, source="live")
            ts = jn.load_signal(signal_id)
            if ts is not None and ts.state is SignalState.OPEN:
                ts.move(SignalState.CLOSED, day, reason)
                jn.upsert_signal(ts)
            typer.echo(
                f"closed: net Rs {trade.net_pnl:,.0f} ({trade.r_multiple:+.2f}R), "
                f"costs Rs {trade.costs:,.0f}"
            )
        else:
            jn.save_position(pos, source="live")
            typer.echo(f"partial: {pos.qty_open} left, stop {pos.stop}")


@journal_app.command("stop")
def journal_stop(
    signal_id: str = typer.Argument(...),
    new_stop: float = typer.Option(..., "--to"),
    journal: Path = JOURNAL_OPTION,
) -> None:
    """Move a stop (only ever up - PLAN.md 1.2)."""
    from tradedesk.broker.indstocks.models import IST
    from tradedesk.journal import Journal

    with Journal(journal) as jn:
        pos = next((p for p in jn.open_positions(source="live") if p.signal.id == signal_id), None)
        if pos is None:
            raise typer.BadParameter(f"no open live position for {signal_id}")
        if new_stop < pos.stop:
            raise typer.BadParameter(f"stops are never widened: {new_stop} < {pos.stop}")
        jn.record_stop_update(signal_id, datetime.now(IST).date(), pos.stop, new_stop, "manual")
        pos.stop = new_stop
        jn.save_position(pos, source="live")
    typer.echo(f"stop -> {new_stop}")


@journal_app.command("positions")
def journal_positions(journal: Path = JOURNAL_OPTION) -> None:
    """Open live and paper positions."""
    from tradedesk.journal import Journal

    with Journal(journal) as jn:
        for source in ("live", "paper"):
            rows = jn.open_positions(source=source)
            typer.echo(f"{source}: {len(rows)} open")
            for p in rows:
                typer.echo(
                    f"  {p.signal.symbol:<12}{p.signal.id:<40}qty {p.qty_open:<6}"
                    f"entry {p.entry_price:<10.2f}stop {p.stop:<10.2f}"
                    f"sessions {p.sessions_held}{' half' if p.partial_done else ''}"
                )


@journal_app.command("tag")
def journal_tag(
    signal_id: str = typer.Argument(...),
    tag: str = typer.Argument(..., help="rule_break | fomo | revenge | note"),
    note: str = typer.Argument(""),
    journal: Path = JOURNAL_OPTION,
) -> None:
    """Tag a trade (rule breaks, FOMO entries, revenge trades) for the weekly review."""
    from tradedesk.journal import Journal

    with Journal(journal) as jn:
        jn.tag(signal_id, tag, note)
    typer.echo("tagged")


@journal_app.command("stats")
def journal_stats(journal: Path = JOURNAL_OPTION) -> None:
    """Paper vs live expectancy, per-setup track records (auto-bench), rule adherence."""
    import json as _json

    from tradedesk.journal import Journal
    from tradedesk.journal.stats import summary

    with Journal(journal) as jn:
        typer.echo(_json.dumps(summary(jn), indent=2))


@journal_app.command("orders")
def journal_orders(journal: Path = JOURNAL_OPTION) -> None:
    """Raw order updates captured from the broker feed (to match with your fills)."""
    from tradedesk.journal import Journal

    with Journal(journal) as jn:
        for r in jn.order_updates():
            typer.echo(
                f"  {r['at']}  {r['order_id']:<22}{r['status']:<20}"
                f"{r['filled_qty']} @ {r['average_price']}"
            )


@paper_app.command("update")
def paper_update(
    on: str | None = typer.Option(None, "--date", help="Session to apply; default: last stored"),
    db: Path = DB_OPTION,
    journal: Path = JOURNAL_OPTION,
    root: Path = ROOT_OPTION,
) -> None:
    """After the close (and `data load`): mark open paper positions with the session's bar
    and open paper positions for every signal that triggered that day."""
    from tradedesk.backtest.runner import prepare_market
    from tradedesk.broker.indstocks.models import Interval
    from tradedesk.journal import Journal
    from tradedesk.paper import PaperBook
    from tradedesk.paper.book import bar_from_row
    from tradedesk.scan import scan_config

    settings = load_config(root)
    with _store(db) as store, Journal(journal) as jn:
        ref = _reference_code(store, root)
        day = (
            datetime.strptime(on, "%Y-%m-%d").date()
            if on
            else store.last_ts(ref, Interval.D1).date()  # type: ignore[union-attr]
        )
        book = PaperBook(jn, settings.risk, capital=float(settings.risk.trading_capital),
                         slippage_pct=float(settings.risk.costs.slippage_pct))  # fmt: skip
        triggered = jn.triggered_between(day, day)
        codes = sorted(
            {p.signal.scrip_code for p in book.positions.values()}
            | {t.signal.scrip_code for t, _, _ in triggered}
        )
        if not codes:
            typer.echo(f"{day}: nothing to do (no open paper positions, no triggers)")
            raise typer.Exit(code=0)
        cfg = scan_config(settings, day)
        cfg.use_intraday = False
        md = prepare_market(store, codes, ref, cfg)
        bars = {}
        for code in codes:
            i = md.pos_by_date.get(code, {}).get(day)
            if i is not None:
                bars[code] = bar_from_row(day, md.features[code].iloc[i])
        closed = book.mark(day, bars)
        for t in closed:
            typer.echo(
                f"  closed {t.scrip_code}: {t.exit_reason} {t.r_multiple:+.2f}R "
                f"net Rs {t.net_pnl:,.0f}"
            )
        opened = 0
        for ts, _, fill in triggered:
            code = ts.signal.scrip_code
            i = md.pos_by_date.get(code, {}).get(day)
            if i is None or fill is None:
                continue
            feats = md.features[code].iloc[: i + 1]
            if book.open_from_trigger(ts, day, fill, feats) is not None:
                opened += 1
        typer.echo(
            f"{day}: {len(closed)} closed, {opened} opened, {len(book.positions)} open; "
            f"paper equity Rs {book.equity:,.0f}"
        )


# --------------------------------------------------------------- M10: Claude


@app.command()
def review(
    what: str = typer.Argument("week", help="week"),
    on: str | None = typer.Option(None, "--date", help="Any date in the week; default today"),
    out_dir: Path = typer.Option(Path("data/reviews"), "--out-dir"),
    journal: Path = JOURNAL_OPTION,
    root: Path = ROOT_OPTION,
) -> None:
    """Weekly coach: Claude reads the journal and paper book and names one change."""
    from tradedesk.broker.indstocks.models import IST
    from tradedesk.claude import ClaudeAdvisor
    from tradedesk.claude.weekly_review import run_weekly_review
    from tradedesk.journal import Journal

    if what != "week":
        raise typer.BadParameter("only `review week` exists")
    settings = load_config(root)
    if settings.claude.mode == "off":
        raise typer.BadParameter("config/claude.yaml mode is off; set notify or veto")
    day = datetime.strptime(on, "%Y-%m-%d").date() if on else datetime.now(IST).date()
    with Journal(journal) as jn:
        path, rev = run_weekly_review(ClaudeAdvisor(settings.claude), jn, day, out_dir)
    if path is None or rev is None:
        typer.echo("no review produced (API failure or spend cap); see logs", err=True)
        raise typer.Exit(code=1)
    typer.echo(path.read_text(encoding="utf-8"))
    typer.echo(f"saved {path}")


@app.command()
def ml(
    what: str = typer.Argument("check-drift", help="check-drift"),
    shadow_log: Path = typer.Option(Path("data/models/shadow.jsonl"), "--shadow-log"),
    journal: Path = JOURNAL_OPTION,
    review_path: Path = typer.Option(Path("data/reviews/queue.jsonl"), "--review-path"),
    root: Path = ROOT_OPTION,
) -> None:
    """Close the loop on prediction/calibration.py::drift_check(): it was a pure function
    nothing in production ever called. This resolves outcomes for shadow-logged signals and
    flags a review-queue item (never auto-pauses config/ml.yaml) if a well-populated
    probability bucket has drifted from reality.

    Outcome proxy: a shadow-logged signal's outcome is read from the NSE paper book
    (`journal.trades(source="paper")`), not re-derived via the exact triple-barrier rule the
    model trains on - `r_multiple > 0` is a real, already-resolved economic outcome and a
    reasonable drift proxy, but it is not literally "hit T1 before the stop". Building the
    exact triple-barrier resolution for live signals would need the same MarketData/feature
    pipeline build_dataset() uses for backtests, which is out of scope for closing this one
    gap - see the ML training plan for why this was scoped as a proxy, not exact."""
    from tradedesk.journal import Journal
    from tradedesk.prediction.calibration import check_and_flag_drift

    if what != "check-drift":
        raise typer.BadParameter("only `ml check-drift` exists")
    _ = load_config(root)
    with Journal(journal) as jn:
        outcomes = {
            row["signal_id"]: (1 if row["r_multiple"] > 0 else 0)
            for row in jn.trades(source="paper")
        }
    if not outcomes:
        typer.echo("no resolved paper trades yet; nothing to check")
        return
    report = check_and_flag_drift(shadow_log, outcomes, review_path=review_path)
    typer.echo(f"paused={report.paused}: {report.reason}")
    for lo, mp, rr, n in report.buckets:
        typer.echo(f"  bucket {lo:.2f}+: predicted={mp:.2f} realised={rr:.2f} n={n}")
    if report.paused:
        typer.echo(f"flagged for review: {review_path}")


@app.command()
def mcp() -> None:
    """Serve the read-only MCP server over stdio (registered in .mcp.json for Claude Code)."""
    from tradedesk.mcp_server import main

    main()


def _not_yet(milestone: str) -> None:
    typer.echo(f"not implemented until Milestone {milestone} (see PLAN.md 14)", err=True)
    raise typer.Exit(code=2)


@app.command()
def live(
    watchlist: Path | None = typer.Option(
        None, "--watchlist", help="Watchlist JSON; default: newest for --market's folder"
    ),
    record_dir: Path | None = typer.Option(
        None, "--record-dir", help="Default: data/sessions, or data/sessions/<market> if not nse"
    ),
    until: str = typer.Option("15:35", "--until", help="HH:MM IST to stop"),
    all_entries: bool = typer.Option(False, "--all", help="Watch C-grade entries too"),
    with_dashboard: bool = typer.Option(True, "--dashboard/--no-dashboard"),
    journal: Path | None = typer.Option(
        None, "--journal", help="Default: data/journal.sqlite, or data/<market>_journal.sqlite"
    ),
    market: str = MARKET_OPTION,
    root: Path = ROOT_OPTION,
) -> None:
    """Market-hours session: stream prices for the watchlist, confirm triggers on 15-minute
    closes, watch positions, route alerts (console, desktop, Telegram, dashboard), journal
    everything and record the session for replay. `--market bse` uses the same INDstocks
    broker/feed as NSE (verified: ws_code()'s EXCH:TOKEN split is already market-agnostic) -
    it just needs its own watchlist folder, journal and session recordings so it never mixes
    with NSE's (paper/book.py and `review week` stay NSE-only, so a BSE journal is a plain
    trigger record for now, not something a paper book grades yet)."""
    from tradedesk.broker.indstocks.models import IST
    from tradedesk.dashboard import DashboardState, create_app, serve
    from tradedesk.journal import Journal
    from tradedesk.live.models import Alert, SessionRules
    from tradedesk.live.session import apply_decision, run_session, signals_from_watchlist
    from tradedesk.live.trigger_monitor import TriggerMonitor
    from tradedesk.scan import load_watchlist

    settings = load_config(root)
    if journal is None:
        journal = Path(f"data/{market}_journal.sqlite") if market != "nse" else JOURNAL_OPTION.default  # noqa: E501
    if record_dir is None:
        record_dir = Path("data/sessions") / market if market != "nse" else Path("data/sessions")
    entry_rules = settings.setups.entry
    rules = SessionRules(
        no_entry_before=datetime.strptime(settings.risk.no_entry_window.end, "%H:%M").time(),
        late_trigger_after=datetime.strptime(entry_rules.late_trigger_after, "%H:%M").time(),
        bar_minutes=entry_rules.confirm_timeframe_minutes,
    )
    if watchlist is None:
        wl_dir = Path("data/watchlists") / market if market != "nse" else Path("data/watchlists")
        candidates = sorted(wl_dir.glob("*.json"))
        if not candidates:
            raise typer.BadParameter(f"no watchlist found in {wl_dir}; run `tradedesk scan --market {market}` first")  # noqa: E501
        watchlist = candidates[-1]
    wl = load_watchlist(watchlist)
    signals = signals_from_watchlist(wl, alertable_only=not all_entries)
    if not signals:
        typer.echo(f"{watchlist}: nothing alertable on the watchlist; exiting")
        raise typer.Exit(code=0)
    jn = Journal(journal)
    positions = jn.open_positions(source="live")
    for t in signals:
        jn.upsert_signal(t)
    codes = sorted(
        {t.signal.scrip_code for t in signals} | {p.signal.scrip_code for p in positions}
    )
    atr = {t.signal.scrip_code: t.signal.atr for t in signals}
    atr.update({p.signal.scrip_code: p.signal.atr for p in positions})
    typer.echo(
        f"watching {len(codes)} instruments ({len(signals)} signals, {len(positions)} positions) "
        f"from {watchlist} until {until} IST"
    )

    state = DashboardState() if with_dashboard else None
    if state is not None:
        state.set_watchlist(wl)
    router = _build_router(settings, wl, state)
    notes = None
    if settings.claude.mode != "off":
        from tradedesk.claude import ClaudeAdvisor
        from tradedesk.claude.trigger_note import TriggerNoteWorker

        def emit_note(a: Alert) -> None:
            typer.echo(f"{a.at:%H:%M:%S} [NOTE   ] {a.message}")
            router.route(a)

        notes = TriggerNoteWorker(
            ClaudeAdvisor(settings.claude),
            wl,
            emit_note,
            bars_for=lambda code: [b for b in monitor.bars_seen if b.scrip_code == code],
        )

    def on_alert(a: Alert) -> None:
        typer.echo(f"{a.at:%H:%M:%S} [{a.level.value.upper():<7}] {a.kind.value:<15} {a.message}")
        router.route(a)
        if notes is not None:
            notes.on_alert(a)  # enqueue only; the note arrives later, after the alert
        jn.record_alert(a)
        for t in monitor.signals:
            if t.signal.scrip_code == a.scrip_code:
                jn.upsert_signal(t)

    monitor = TriggerMonitor(rules, signals=signals, positions=positions, atr_by_code=atr)
    today = datetime.now(IST).date()
    recording = record_dir / f"{today.isoformat()}.jsonl"

    async def alert_worker(stop: asyncio.Event) -> None:
        await router.worker(stop)

    extra: list[Callable[[asyncio.Event], Awaitable[None]]] = [alert_worker]
    if notes is not None:
        extra.append(notes.run)
    if router.telegram is not None:
        bot = router.telegram

        def decide(sid: str, action: str) -> None:
            if apply_decision(monitor.signals, sid, action, today):
                for t in monitor.signals:
                    if t.signal.id == sid:
                        jn.upsert_signal(t)

        async def poll(stop: asyncio.Event) -> None:
            await bot.poll_forever(decide, stop)

        extra.append(poll)
    if state is not None:
        host, port = settings.alerts.dashboard.host, settings.alerts.dashboard.port
        typer.echo(f"dashboard at http://{host}:{port}")

        async def dash(stop: asyncio.Event) -> None:
            import uvicorn

            server = uvicorn.Server(
                uvicorn.Config(
                    create_app(state, journal_path=journal),
                    host=host,
                    port=port,
                    log_level="warning",
                )
            )
            task = asyncio.create_task(server.serve())
            await stop.wait()
            server.should_exit = True
            await task

        extra.append(dash)

    async def go(c: IndstocksClient) -> None:
        from tradedesk.broker.indstocks import OrderUpdatesFeed

        async def orders(stop: asyncio.Event) -> None:
            feed = OrderUpdatesFeed(c.tokens, jn.record_order_update)
            await feed.run(stop)

        await run_session(
            c,
            monitor,
            codes=codes,
            rules=rules,
            recording=recording,
            on_alert=on_alert,
            until=datetime.strptime(until, "%H:%M").time(),
            dashboard=state,
            extra_tasks=[*extra, orders],
        )

    try:
        asyncio.run(_with_client(go))
    finally:
        for t in monitor.signals:
            jn.upsert_signal(t)
        for p in monitor.positions.values():
            jn.save_position(p, source="live")
        jn.close()
    _ = serve
    triggered = [t.signal.symbol for t in monitor.signals if t.state.value == "triggered"]
    typer.echo(f"session over: {len(monitor.alerts)} alerts, triggered {triggered or 'none'}")
    typer.echo(f"recording: {recording}")


@app.command()
def scan(
    on: str = typer.Option("today", "--date", help="YYYY-MM-DD or 'today' (last stored session)"),
    setup: list[str] = typer.Option(None, "--setup", help="Override enabled setups"),
    charts: bool = typer.Option(False, "--charts", help="Render a PNG per active entry"),
    include_rejected: bool = typer.Option(True, "--rejected/--no-rejected"),
    out_dir: Path = typer.Option(Path("data/watchlists"), "--out-dir"),
    claude_mode: str | None = typer.Option(
        None, "--claude", help="off|notify|veto; default from config/claude.yaml"
    ),
    db: Path = DB_OPTION,
    journal: Path = JOURNAL_OPTION,
    root: Path = ROOT_OPTION,
    market: str = MARKET_OPTION,
) -> None:
    """Evening scan: build, print and save tomorrow's watchlist from stored daily candles.
    With Claude enabled (config or --claude), the top setups get a three-line chart read."""
    from tradedesk.alerts.charts import render_signal_chart
    from tradedesk.backtest.runner import prepare_market as prep_market
    from tradedesk.broker.indstocks.models import Interval
    from tradedesk.engine.signals import SetupKind
    from tradedesk.markets import bse_market, crypto_market, nse_market
    from tradedesk.scan import build_watchlist, render_text, save_watchlist, scan_config

    settings = load_config(root)
    db = _resolve_db(db, market)
    mkt = {"crypto": crypto_market, "bse": bse_market}.get(market, nse_market)(settings)
    kinds = [SetupKind(k) for k in setup] if setup else None
    with _store(db) as store:
        if market == "crypto":
            ref = f"{mkt.code_prefix}{mkt.benchmark_name}"  # CDX_BTCINR - trades every day
            vix = None
        elif market == "bse":
            ref = _reference_code(store, root, mkt.benchmark_name, exch="BSE")  # SENSEX
            vix = None  # bse_market.vix_required is False - see its docstring
        else:
            ref = _reference_code(store, root)
            vix = store.index_code(settings.universe.volatility_index)
        if on == "today":
            last = store.last_ts(ref, Interval.D1)
            if last is None:
                raise typer.BadParameter("no benchmark candles stored; run `tradedesk data load`")
            day = last.date()
        else:
            day = datetime.strptime(on, "%Y-%m-%d").date()
        cfg = scan_config(settings, day, kinds, market=mkt)
        cfg.vix_code = vix
        if market == "nse":
            cfg.sector_of, cfg.sector_codes = _sector_config(store, root)
        codes = [c for c in store.codes(Interval.D1) if c not in (ref, vix)]
        typer.echo(f"scanning {len(codes)} codes as of {day} with {[k.value for k in cfg.setups]}")
        md = prep_market(store, codes, ref, cfg)
    from tradedesk.journal import Journal
    from tradedesk.journal.stats import track_records
    from tradedesk.scan import OpenPositionInfo

    with Journal(journal) as jn:
        held = [
            OpenPositionInfo(p.signal.scrip_code, p.entry_price, p.stop, p.qty_open)
            for p in jn.open_positions(source="live")
        ]
        records = track_records(jn, [k.value for k in cfg.setups])
    wl = build_watchlist(
        md, cfg, settings, day, open_positions=held, track_records=records, market=mkt
    )
    typer.echo(render_text(wl, include_rejected=include_rejected))
    if charts:
        chart_dir = out_dir / day.isoformat()
        entries = []
        for e in wl.entries:
            if not e.on_watchlist:
                entries.append(e)
                continue
            code = e.signal.scrip_code
            feats = md.features[code].iloc[: md.pos_by_date[code][day] + 1]
            png = render_signal_chart(feats, e.signal, chart_dir / f"{e.signal.symbol}.png")
            typer.echo(f"  chart {png}")
            entries.append(e.model_copy(update={"chart_path": str(png)}))
        wl = wl.model_copy(update={"entries": entries})
    mode = claude_mode or settings.claude.mode
    if mode != "off":
        from tradedesk.claude import ClaudeAdvisor
        from tradedesk.claude.chart_read import apply_reads, read_watchlist

        advisor = ClaudeAdvisor(settings.claude.model_copy(update={"mode": mode}))
        reads = read_watchlist(advisor, wl, max_setups=settings.claude.max_setups_per_evening)
        wl = apply_reads(wl, reads, mode)
        typer.echo(
            f"claude ({mode}): {len(reads)} chart reads, "
            f"month spend ${advisor.month_spend_usd():.2f}"
        )
        for e in wl.entries:
            for n in e.score_notes:
                if n.startswith("Claude ("):
                    typer.echo(f"  {e.signal.symbol}: {n}")
    bundle = None
    if settings.ml.enabled or settings.ml.shadow:
        from tradedesk.prediction import latest_bundle

        bundle = latest_bundle(Path("data/models"))
    if bundle is not None:
        from tradedesk.prediction.predict import score_watchlist

        wl, probs = score_watchlist(
            bundle, wl, md, settings.ml, shadow_log=Path("data/models/shadow.jsonl")
        )
        label = "shadow" if (settings.ml.shadow or not settings.ml.enabled) else "ENABLED"
        typer.echo(
            f"model {bundle.version} ({label}): "
            + ", ".join(f"{k} {v:.2f}" for k, v in probs.items())
        )
    # Filenames are date-only ("{date}.json"), and `dashboard`/`live`'s auto-discovery
    # just takes the newest file in the folder with no market check - a crypto scan and
    # an NSE scan sharing a date would silently overwrite or shadow each other (found
    # 2026-09-12 while building the dashboard's Performance tab: a manual crypto scan
    # made `tradedesk dashboard` load BTC/ETH as "today's watchlist"). Give crypto its
    # own subfolder so the flat data/watchlists/ stays NSE-only, matching what those
    # commands assume.
    if market in ("crypto", "bse"):
        out_dir = out_dir / market
    path = save_watchlist(wl, out_dir)
    typer.echo(f"saved {path}")


@app.command()
def backtest(
    setup: list[str] = typer.Option(
        ["base_breakout"], "--setup", help="Setup name; repeat for several"
    ),
    from_: str = typer.Option(..., "--from", help="YYYY-MM-DD"),
    to: str | None = typer.Option(None, "--to", help="YYYY-MM-DD; default today"),
    split: str | None = typer.Option(
        None, "--split", help="Walk-forward split date: report in/out of sample separately"
    ),
    capital: float | None = typer.Option(None, "--capital", help="Default: config trading_capital"),
    codes: list[str] = typer.Option(None, "--code", help="Restrict to these scrip codes"),
    out: Path | None = typer.Option(None, "--out", help="Write trades CSV"),
    db: Path = DB_OPTION,
    root: Path = ROOT_OPTION,
    market: str = MARKET_OPTION,
) -> None:
    """Event-driven backtest on stored daily candles (gap-aware fills, portfolio limits)."""
    from tradedesk.backtest import (
        BacktestConfig,
        build_report,
        prepare_market,
        run_backtest,
        walk_forward,
    )
    from tradedesk.broker.indstocks.models import IST, Interval
    from tradedesk.engine.signals import SetupKind
    from tradedesk.markets import bse_market, crypto_market, nse_market

    settings = load_config(root)
    db = _resolve_db(db, market)
    mkt = {"crypto": crypto_market, "bse": bse_market}.get(market, nse_market)(settings)
    kinds = [SetupKind(s) for s in setup]
    start = datetime.strptime(from_, "%Y-%m-%d").date()
    end = datetime.strptime(to, "%Y-%m-%d").date() if to else datetime.now(IST).date()
    cfg = BacktestConfig(
        setups=kinds,
        start=start,
        end=end,
        capital=capital or float(settings.risk.trading_capital),
        risk=settings.risk,
        engine=settings.engine,
        setup_params={k: v.model_dump() for k, v in settings.setups.setups.items()},
        universe_rules=mkt.universe_rules,
        slippage_pct=float(mkt.costs.slippage_pct),
        costs=mkt.costs,
        qty_step=mkt.qty_step,
        min_notional=mkt.min_notional_inr,
        vix_required=mkt.vix_required,
    )
    with _store(db) as store:
        if market == "crypto":
            ref = f"{mkt.code_prefix}{mkt.benchmark_name}"
            vix = None
        elif market == "bse":
            ref = _reference_code(store, root, mkt.benchmark_name, exch="BSE")
            vix = None
        else:
            ref = _reference_code(store, root)
            vix = store.index_code(settings.universe.volatility_index)
        cfg.vix_code = vix
        if market == "nse":
            cfg.sector_of, cfg.sector_codes = _sector_config(store, root)
        universe = (
            list(codes) if codes else [c for c in store.codes(Interval.D1) if c != ref and c != vix]
        )
        typer.echo(
            f"preparing {len(universe)} codes, {start} -> {end}, setups {[k.value for k in kinds]}"
        )
        md = prepare_market(store, universe, ref, cfg)
    typer.echo(f"{len(md.features)} codes with history; {len(md.calendar)} sessions loaded")
    result = run_backtest(md, cfg)
    if split:
        split_date = datetime.strptime(split, "%Y-%m-%d").date()
        ins, outs = walk_forward(result, split_date)
        typer.echo(f"=== in sample (< {split_date}) ===")
        typer.echo(ins.text())
        typer.echo(f"=== out of sample (>= {split_date}) ===")
        typer.echo(outs.text())
    else:
        typer.echo(build_report(result).text())
    typer.echo(
        "caveat: universe is built from the candles on hand; stocks the API no longer serves "
        "are missing (survivorship bias) - treat results as optimistic."
    )
    if out is not None:
        import pandas as pd

        rows = [
            {
                "setup": t.setup, "code": t.scrip_code, "entry": t.entry_date, "exit": t.exit_date,
                "sessions": t.sessions_held, "qty": t.position.qty_initial,
                "entry_px": round(t.position.entry_price, 2),
                "stop": round(t.position.signal.stop, 2),
                "net_pnl": round(t.net_pnl, 2), "r": round(t.r_multiple, 2),
                "costs": round(t.costs, 2),
                "exit_reason": t.exit_reason,
            }
            for t in result.portfolio.closed
        ]  # fmt: skip
        out.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(out, index=False)
        typer.echo(f"written {out}")


@app.command()
def replay(
    session: Path = typer.Argument(..., exists=True, help="Recorded session JSONL"),
    watchlist: Path = typer.Option(..., "--watchlist", help="Watchlist JSON in force that day"),
    all_entries: bool = typer.Option(False, "--all"),
    send: bool = typer.Option(False, "--alerts", help="Also send through the enabled channels"),
    root: Path = ROOT_OPTION,
) -> None:
    """Replay a recorded session through the trigger monitor and print the alerts."""
    from tradedesk.live.models import SessionRules
    from tradedesk.live.session import signals_from_watchlist
    from tradedesk.live.trigger_monitor import TriggerMonitor
    from tradedesk.replay import read_session
    from tradedesk.replay import replay as run_replay
    from tradedesk.scan import load_watchlist

    settings = load_config(root)
    entry_rules = settings.setups.entry
    rules = SessionRules(
        no_entry_before=datetime.strptime(settings.risk.no_entry_window.end, "%H:%M").time(),
        late_trigger_after=datetime.strptime(entry_rules.late_trigger_after, "%H:%M").time(),
        bar_minutes=entry_rules.confirm_timeframe_minutes,
    )
    wl = load_watchlist(watchlist)
    signals = signals_from_watchlist(wl, alertable_only=not all_entries)
    router = _build_router(settings, wl, None) if send else None
    monitor = TriggerMonitor(
        rules,
        signals=signals,
        atr_by_code={t.signal.scrip_code: t.signal.atr for t in signals},
        on_alert=(lambda a: None if router is None else (router.route(a), None)[1]),
    )
    result = run_replay(read_session(session), monitor)
    if router is not None:
        typer.echo(f"telegram sent {asyncio.run(router.flush())}")
    for a in result.alerts:
        typer.echo(f"{a.at:%H:%M:%S} [{a.level.value.upper():<7}] {a.kind.value:<15} {a.message}")
    typer.echo(
        f"{result.ticks} ticks, {result.bars} bars, {result.resyncs} resyncs, "
        f"{len(result.alerts)} alerts"
    )
    for t in monitor.signals:
        typer.echo(f"  {t.signal.symbol:<12}{t.state.value}")


@app.command()
def train(
    from_: str = typer.Option(..., "--from", help="YYYY-MM-DD start of the backtest window"),
    to: str | None = typer.Option(None, "--to", help="YYYY-MM-DD; default today"),
    setup: list[str] = typer.Option(None, "--setup", help="Setup name; default: all enabled"),
    shadow: bool = typer.Option(True, "--shadow/--no-shadow", help="Shadow is the only mode"),
    n_splits: int = typer.Option(4, "--splits", help="Purged walk-forward folds"),
    final_test_frac: float = typer.Option(
        0.2, "--final-test-frac", help="Most recent fraction of history locked as a final test"
    ),
    auto_threshold: bool = typer.Option(
        True,
        "--auto-threshold/--fixed-threshold",
        help="Grid-search the probability threshold on validation folds instead of using "
        "ml.yaml's grade_a_min_probability directly",
    ),
    models_dir: Path = typer.Option(Path("data/models"), "--models-dir"),
    dataset_out: Path | None = typer.Option(None, "--dataset", help="Also write the CSV"),
    db: Path = DB_OPTION,
    root: Path = ROOT_OPTION,
) -> None:
    """Train the meta-labeling model (M11): backtest -> triple-barrier labels -> purged
    walk-forward on all but the most recent `--final-test-frac` of history -> calibrated
    baseline (LightGBM/XGBoost only if available and better OOS) -> threshold grid search
    on validation folds -> one locked scoring of the final test set -> saved bundle, plus a
    Strategy A (existing rules) vs Strategy C (rules + ML filter) economic comparison.
    The saved model is used in shadow mode by `tradedesk scan` until ml.yaml enables it."""
    from tradedesk.backtest import prepare_market, run_backtest
    from tradedesk.broker.indstocks.models import IST, Interval
    from tradedesk.engine.signals import SetupKind
    from tradedesk.prediction import build_dataset, compare_strategies
    from tradedesk.prediction import train as train_model
    from tradedesk.scan import scan_config

    if not shadow:
        raise typer.BadParameter("training never switches the model on; edit config/ml.yaml")
    settings = load_config(root)
    kinds = [SetupKind(k) for k in setup] if setup else None
    start = datetime.strptime(from_, "%Y-%m-%d").date()
    end = datetime.strptime(to, "%Y-%m-%d").date() if to else datetime.now(IST).date()
    cfg = scan_config(settings, end, kinds)
    cfg.start = start
    with _store(db) as store:
        ref = _reference_code(store, root)
        vix = store.index_code(settings.universe.volatility_index)
        cfg.vix_code = vix
        cfg.sector_of, cfg.sector_codes = _sector_config(store, root)
        codes = [c for c in store.codes(Interval.D1) if c not in (ref, vix)]
        typer.echo(f"backtesting {len(codes)} codes {start} -> {end} for the dataset")
        md = prepare_market(store, codes, ref, cfg)
    result = run_backtest(md, cfg)
    max_hold = settings.risk.max_hold_sessions
    df = build_dataset(md, result, max_hold=max_hold)
    typer.echo(
        f"dataset: {len(df)} triggered signals, base rate "
        f"{(df['label'].mean() if len(df) else float('nan')):.2f}"
    )
    if dataset_out is not None:
        dataset_out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(dataset_out, index=False)
        typer.echo(f"dataset written to {dataset_out}")
    rep = train_model(
        df,
        n_splits=n_splits,
        embargo_sessions=settings.ml.embargo_sessions,
        threshold=float(settings.ml.grade_a_min_probability),
        final_test_frac=final_test_frac,
        auto_threshold=auto_threshold,
        max_hold=max_hold,
    )
    typer.echo(rep.text())
    if rep.bundle is None:
        typer.echo("no model saved", err=True)
        raise typer.Exit(code=1)
    comparison = compare_strategies(result, rep.bundle, df, starting_capital=cfg.capital)
    typer.echo(f"strategy A vs C (existing rules vs rules+ML): {comparison}")
    path = rep.bundle.save(models_dir)
    typer.echo(f"saved {path} (shadow only; ml.yaml enabled={settings.ml.enabled})")


if __name__ == "__main__":
    app()
