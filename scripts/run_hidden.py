"""Run a script under pythonw.exe (no console window) with its output sent to a log file.

Usage (scheduled task): pythonw.exe scripts/run_hidden.py <log-name> <script.py> [args...]
The script runs exactly as `python <script.py> [args...]` would; the special name `tradedesk`
runs the console script (`tradedesk [args...]`) instead. stdout/stderr are appended to
data/logs/<log-name>.log, and the exit code is preserved.
"""

from __future__ import annotations

import runpy
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    log_name, script, *args = sys.argv[1:]
    log_dir = ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    target = Path(script)
    if script != "tradedesk" and not target.is_absolute():
        target = ROOT / target
    with (log_dir / f"{log_name}.log").open("a", encoding="utf-8", buffering=1) as log:
        sys.stdout = sys.stderr = log
        log.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} {target.name} {args}\n")
        sys.argv = [str(target), *args]
        try:
            if script == "tradedesk":
                sys.path.insert(0, str(ROOT))  # a console script gets no repo root
                from tradedesk.cli import app

                app()
            else:
                sys.path.insert(0, str(target.parent))
                runpy.run_path(str(target), run_name="__main__")
        except SystemExit as exit_:
            code = exit_.code
            return code if isinstance(code, int) else (0 if code is None else 1)
        except BaseException:
            import traceback

            traceback.print_exc()
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
