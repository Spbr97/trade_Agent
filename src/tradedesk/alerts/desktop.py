"""Desktop notification with sound (PLAN.md 7). Windows: a toast via PowerShell/WinRT (no
extra packages, so nothing for Smart App Control to block) and `winsound` for the beep.
Best effort: any failure is logged, never raised - the alert still reaches the console,
the dashboard and Telegram."""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

from tradedesk.alerts.cards import OutboundMessage

log = logging.getLogger(__name__)

_TOAST_PS = r"""
$ns = "Windows.UI.Notifications"
[Windows.UI.Notifications.ToastNotificationManager, $ns, ContentType = WindowsRuntime] | Out-Null
$xns = "Windows.Data.Xml.Dom.XmlDocument"
[Windows.Data.Xml.Dom.XmlDocument, $xns, ContentType = WindowsRuntime] | Out-Null
$template = @"
<toast duration="long"><visual><binding template="ToastGeneric">
<text>{title}</text><text>{body}</text>
</binding></visual></toast>
"@
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("tradedesk")
$notifier.Show($toast)
"""


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _powershell_toast(title: str, body: str) -> None:
    script = _TOAST_PS.replace("{title}", _escape(title)).replace("{body}", _escape(body))
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        capture_output=True,
        timeout=15,
    )


def _beep(urgent: bool) -> None:
    if sys.platform != "win32":
        return
    import winsound

    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION if urgent else winsound.MB_OK)


@dataclass
class DesktopNotifier:
    sound: bool = True
    toast: Callable[[str, str], None] = _powershell_toast
    beep: Callable[[bool], None] = _beep
    sent: int = 0

    def send(self, msg: OutboundMessage) -> bool:
        body = msg.body if len(msg.body) <= 400 else msg.body[:397] + "..."
        try:
            if sys.platform == "win32":
                self.toast(msg.title, body)
            if self.sound:
                self.beep(msg.urgent)
            self.sent += 1
            return True
        except Exception as exc:  # noqa: BLE001 - notifications are best effort
            log.warning("desktop notification failed: %s", exc)
            return False
