"""`tradedesk` command line. M1: `config check`, `costs`. M2: `auth`, `instruments`,
`candles`, `quote`, `stream`. Later milestones' commands are registered so the list in
CLAUDE.md is accurate and fail with the milestone that implements them."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from pydantic import ValidationError

from tradedesk.config import load_config
from tradedesk.models import TradeType
from tradedesk.risk.costs import LegCost, net_reward_risk, round_trip_cost

if TYPE_CHECKING:
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

DB_OPTION = typer.Option(Path("data/tradedesk.duckdb"), "--db", help="DuckDB file.")


ROOT_OPTION = typer.Option(Path("."), "--root", help="Project root containing config/.")


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
        KEY_TOTP_SECRET,
        KeyringStore,
    )

    store = KeyringStore()
    for k in (KEY_CLIENT_ID, KEY_MPIN, KEY_TOTP_SECRET):
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


def _reference_code(store: CandleStore, root: Path) -> str:
    name = load_config(root).universe.benchmark
    code = store.index_code(name)
    if code is None:
        raise typer.BadParameter(
            f"benchmark {name!r} not in instruments table; run `tradedesk data sync-instruments`"
        )
    return code


@data_app.command("sync-instruments")
def data_sync_instruments(db: Path = DB_OPTION) -> None:
    """Download the equity and index masters into the store's instruments table."""

    async def go(c: IndstocksClient) -> None:
        from tradedesk.broker.indstocks.instruments import nse_cash_equities

        eq = await c.equity_instruments()
        idx = await c.index_instruments()
        with _store(db) as store:
            n = store.upsert_instruments([*nse_cash_equities(eq), *idx])
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
) -> None:
    """Pull candle history into the store (incremental: only new bars are fetched)."""
    from tradedesk.broker.indstocks.models import IST, Interval
    from tradedesk.data.history_loader import LoadResult, default_start, load_history

    iv = Interval(interval)
    now = datetime.now(IST)
    start = now - timedelta(days=365 * years) if years else default_start(iv, now)

    async def go(c: IndstocksClient) -> None:
        with _store(db) as store:
            targets = list(codes) if codes else store.instrument_codes(kind="equity")
            ref = store.index_code(load_config(root).universe.benchmark)
            if ref and ref not in targets:
                targets.append(ref)
            if not targets:
                raise typer.BadParameter("no codes: pass scrip codes or run data sync-instruments")
            done = 0

            def progress(r: LoadResult) -> None:
                nonlocal done
                done += 1
                if done % 25 == 0 or r.error:
                    typer.echo(
                        f"  {done}/{len(targets)} {r.scrip_code}: {r.fetched} bars {r.error or ''}"
                    )

            typer.echo(
                f"loading {iv.value} for {len(targets)} codes from {start:%Y-%m-%d} into {db}"
            )
            summary = await load_history(
                c, store, targets, iv, start=start, end=now, progress=progress
            )
            typer.echo(f"fetched {summary.fetched} bars; {len(summary.errors)} errors")
            for r in summary.errors[:20]:
                typer.echo(f"  ERROR {r.scrip_code}: {r.error}")
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
) -> None:
    """Data-quality report: gaps, bad bars, big jumps, suspected unadjusted splits, staleness."""
    from tradedesk.broker.indstocks.models import Interval
    from tradedesk.data.health import run_quality_report

    with _store(db) as store:
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
) -> None:
    """List the liquid universe as of a date, from the rules in config/universe.yaml."""
    from tradedesk.broker.indstocks.models import IST
    from tradedesk.data.universe import UniverseRules, universe_on

    cfg = load_config(root).universe
    rules = UniverseRules(
        min_avg_turnover_inr=float(cfg.min_avg_daily_turnover_inr), min_price=float(cfg.min_price)
    )
    day = datetime.strptime(on, "%Y-%m-%d").date() if on else datetime.now(IST).date()
    with _store(db) as store:
        members = universe_on(store, store.instrument_codes(kind="equity"), day, rules)
        names = [f"{c} {store.symbol_for(c) or ''}" for c in members]
    typer.echo(f"{len(members)} members on {day}")
    for n in names:
        typer.echo(f"  {n}")


@data_app.command("status")
def data_status(db: Path = DB_OPTION) -> None:
    """What the store holds."""
    from tradedesk.broker.indstocks.models import Interval

    with _store(db) as store:
        for iv in (Interval.D1, Interval.H1, Interval.M15):
            codes = store.codes(iv)
            if not codes:
                continue
            stamps = [t for c in codes if (t := store.last_ts(c, iv)) is not None]
            when = f"{max(stamps):%Y-%m-%d %H:%M}" if stamps else "-"
            typer.echo(f"  {iv.value:<10}{len(codes):>5} codes, latest bar {when}")
        n_inst = store.con.execute("SELECT count(*) FROM instruments").fetchone()
        n_ca = store.con.execute("SELECT count(*) FROM corporate_actions").fetchone()
        n_re = store.con.execute("SELECT count(*) FROM results_events").fetchone()
        typer.echo(
            f"  instruments {n_inst[0] if n_inst else 0}, "
            f"corporate actions {n_ca[0] if n_ca else 0}, "
            f"results events {n_re[0] if n_re else 0}"
        )


def _not_yet(milestone: str) -> None:
    typer.echo(f"not implemented until Milestone {milestone} (see PLAN.md 14)", err=True)
    raise typer.Exit(code=2)


@app.command()
def live() -> None:
    """Market-hours session: trigger monitor and position watch (M7)."""
    _not_yet("M7")


@app.command()
def scan(
    on: str = typer.Option("today", "--date", help="YYYY-MM-DD or 'today' (last stored session)"),
    setup: list[str] = typer.Option(None, "--setup", help="Override enabled setups"),
    charts: bool = typer.Option(False, "--charts", help="Render a PNG per active entry"),
    include_rejected: bool = typer.Option(True, "--rejected/--no-rejected"),
    out_dir: Path = typer.Option(Path("data/watchlists"), "--out-dir"),
    db: Path = DB_OPTION,
    root: Path = ROOT_OPTION,
) -> None:
    """Evening scan: build, print and save tomorrow's watchlist from stored daily candles."""
    from tradedesk.alerts.charts import render_signal_chart
    from tradedesk.backtest.runner import prepare_market
    from tradedesk.broker.indstocks.models import Interval
    from tradedesk.engine.signals import SetupKind
    from tradedesk.scan import build_watchlist, render_text, save_watchlist, scan_config

    settings = load_config(root)
    kinds = [SetupKind(k) for k in setup] if setup else None
    with _store(db) as store:
        ref = _reference_code(store, root)
        vix = store.index_code(settings.universe.volatility_index)
        if on == "today":
            last = store.last_ts(ref, Interval.D1)
            if last is None:
                raise typer.BadParameter("no benchmark candles stored; run `tradedesk data load`")
            day = last.date()
        else:
            day = datetime.strptime(on, "%Y-%m-%d").date()
        cfg = scan_config(settings, day, kinds)
        cfg.vix_code = vix
        codes = [c for c in store.codes(Interval.D1) if c not in (ref, vix)]
        typer.echo(f"scanning {len(codes)} codes as of {day} with {[k.value for k in cfg.setups]}")
        md = prepare_market(store, codes, ref, cfg)
    wl = build_watchlist(md, cfg, settings, day)
    typer.echo(render_text(wl, include_rejected=include_rejected))
    path = save_watchlist(wl, out_dir)
    typer.echo(f"saved {path}")
    if charts:
        chart_dir = out_dir / day.isoformat()
        for e in wl.active:
            code = e.signal.scrip_code
            feats = md.features[code].iloc[: md.pos_by_date[code][day] + 1]
            png = render_signal_chart(feats, e.signal, chart_dir / f"{e.signal.symbol}.png")
            typer.echo(f"  chart {png}")


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
    from tradedesk.data.universe import UniverseRules
    from tradedesk.engine.signals import SetupKind

    settings = load_config(root)
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
        universe_rules=UniverseRules(
            min_avg_turnover_inr=float(settings.universe.min_avg_daily_turnover_inr),
            min_price=float(settings.universe.min_price),
        ),
        slippage_pct=float(settings.risk.costs.slippage_pct),
    )
    with _store(db) as store:
        ref = _reference_code(store, root)
        vix = store.index_code(settings.universe.volatility_index)
        cfg.vix_code = vix
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
def replay(session: str = typer.Argument(...)) -> None:
    """Replay a recorded market-hours session (M7)."""
    _not_yet("M7")


@app.command()
def train(shadow: bool = typer.Option(True, "--shadow/--no-shadow")) -> None:
    """Train the meta-labeling model (M11)."""
    _not_yet("M11")


if __name__ == "__main__":
    app()
