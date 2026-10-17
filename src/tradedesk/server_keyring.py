"""Encrypted keyring file for an unattended Linux server (Windows uses Credential Manager).

`keyrings.cryptfile` encrypts the file but only ever asks for its password at a prompt, which
a timer-started job cannot answer. This backend takes the password from the environment
instead: systemd reads it from the root-only /etc/tradedesk/env (deploy/linux/install.sh) and
hands it to every unit, so no job and no code path ever stores or prints it. Anyone with root
on the machine can read that file, which is true of any unattended setup.

Selected on the server with PYTHON_KEYRING_BACKEND=tradedesk.server_keyring.EnvPasswordKeyring.
Without the variable it behaves exactly like CryptFileKeyring (prompts), so running it by hand
for a one-off is still possible.
"""

from __future__ import annotations

import os
from typing import Any

from keyrings.cryptfile.cryptfile import CryptFileKeyring

PASSWORD_ENV = "KEYRING_CRYPTFILE_PASSWORD"


class EnvPasswordKeyring(CryptFileKeyring):  # type: ignore[misc]
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        password = os.environ.get(PASSWORD_ENV)
        if password:
            self.keyring_key = password
