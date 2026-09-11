"""Signal lifecycle state machine (PLAN.md 6.9).

ARMED -> TRIGGERED | CHASED | EXPIRED | INVALIDATED
TRIGGERED -> TAKEN | SKIPPED
TAKEN -> OPEN -> CLOSED
Transitions outside this table raise, so a bug cannot silently resurrect a dead signal.
The backtester drives ARMED..CLOSED; live (M7) stops at TRIGGERED and lets the human
choose TAKEN/SKIPPED; the paper book treats every TRIGGERED signal as TAKEN.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from tradedesk.engine.signals import Signal


class SignalState(StrEnum):
    ARMED = "armed"
    TRIGGERED = "triggered"
    CHASED = "chased"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"
    TAKEN = "taken"
    SKIPPED = "skipped"
    OPEN = "open"
    CLOSED = "closed"


TRANSITIONS: dict[SignalState, frozenset[SignalState]] = {
    SignalState.ARMED: frozenset(
        {SignalState.TRIGGERED, SignalState.CHASED, SignalState.EXPIRED, SignalState.INVALIDATED}
    ),
    SignalState.TRIGGERED: frozenset({SignalState.TAKEN, SignalState.SKIPPED}),
    SignalState.TAKEN: frozenset({SignalState.OPEN}),
    SignalState.OPEN: frozenset({SignalState.CLOSED}),
    SignalState.CHASED: frozenset(),
    SignalState.EXPIRED: frozenset(),
    SignalState.INVALIDATED: frozenset(),
    SignalState.SKIPPED: frozenset(),
    SignalState.CLOSED: frozenset(),
}

TERMINAL = frozenset(
    {
        SignalState.CHASED,
        SignalState.EXPIRED,
        SignalState.INVALIDATED,
        SignalState.SKIPPED,
        SignalState.CLOSED,
    }
)


class IllegalTransition(RuntimeError):
    pass


class Transition(BaseModel):
    model_config = ConfigDict(frozen=True)

    on: date
    from_state: SignalState
    to_state: SignalState
    note: str = ""


class TrackedSignal(BaseModel):
    """A signal plus its state history. Mutable on purpose: one object per signal."""

    signal: Signal
    state: SignalState = SignalState.ARMED
    history: list[Transition] = Field(default_factory=list)
    sessions_armed: int = 0  # sessions elapsed since arming (for expiry)

    def can(self, to: SignalState) -> bool:
        return to in TRANSITIONS[self.state]

    def move(self, to: SignalState, on: date, note: str = "") -> None:
        if not self.can(to):
            raise IllegalTransition(f"{self.signal.id}: {self.state} -> {to} not allowed")
        self.history.append(Transition(on=on, from_state=self.state, to_state=to, note=note))
        self.state = to

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    def tick_session(self) -> None:
        """Called once per session while ARMED; expiry happens after `valid_sessions`."""
        self.sessions_armed += 1

    @property
    def expired_by_time(self) -> bool:
        return self.sessions_armed > self.signal.valid_sessions
