from __future__ import annotations

TOTP_SECRET = "JBSWY3DPEHPK3PXP"  # RFC 6238 test vector base32


class MemoryStore:
    def __init__(self, **values: str) -> None:
        self.values = dict(values)

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


class FakeClock:
    def __init__(self, t: float = 1_700_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s
