Local configuration
-------------------

This project may need small local changes before use (for example, setting the BLE address of your Wio Terminal).

- Do NOT commit personal device addresses or local environment files (virtualenvs, IDE settings, etc.).
- Edit `scripts/runner.conf` locally and keep `ble_address` commented in the repository. The `run_sender.ps1` script reads environment variables and this file at runtime.

Files to keep local and out of source control:

- `.venv/` or `venv/`
- `.vscode/`
- `wio-terminal/.pio/` build artifacts
- `scripts/runner.conf` (edit locally as needed)
