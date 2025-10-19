# Warning: This is a "vibe-coded" project, please use at your own risk! 

# Wio Terminal PC Monitor

Real-time PC system monitoring display using a Wio Terminal connected via USB serial.

- PC sends metrics every 500 ms over serial (115200 baud)
- Wio Terminal receives and renders a dashboard on its 320x240 TFT

## Structure

- `pc/` — Python sender script and dependencies
- `wio-terminal/` — PlatformIO project for the Wio Terminal firmware

## Requirements

- Windows 10/11 with Python 3.9+
- Wio Terminal with USB cable
- VS Code + PlatformIO extension (for building/flashing the Wio Terminal)

## Setup — Python sender

1. Install Python dependencies:

```powershell
# In the repo root
python -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -r .\pc\requirements.txt
```

2. Find your Wio Terminal COM port (after plugging it in). Common names: `COM3`, `COM4`, etc.

3. Run the sender (replace the port if known; otherwise it will try to auto-detect):

```powershell
# Known port
python .\pc\pc_stats_sender.py --port COM4

# Or let it auto-detect (tries to find a Seeed/Wio device)
python .\pc\pc_stats_sender.py
```

- The sender prints the CSV lines it transmits. Stop with Ctrl+C.

## Setup — Wio Terminal firmware (PlatformIO)

1. Open the `wio-terminal` folder in VS Code (or open the whole repo and select that folder as the PlatformIO project).
2. Build and upload to the Wio Terminal at 115200 baud:
   - In PlatformIO: Build → Upload

The display should show a dashboard that updates as the Python script streams data.

## Serial format

The sender transmits one line every 500 ms:

```
CPU%,CPU_TEMP_C,RAM%,GPU%,GPU_TEMP_C\n
```

Example:

```
42.1,55.4,63.2,38.0,67.0
```

- CPU%, RAM%, GPU% are floats (0-100)
- CPU_TEMP_C and GPU_TEMP_C are floats in Celsius; if unavailable, `-1` is sent and the device shows `N/A`

## Notes

- CPU temperature on Windows can be tricky. The script tries multiple sources (OpenHardwareMonitor WMI, ACPI thermal zone, psutil) and falls back to `-1` if not found.
- Ensure the baud rate matches: both Python and firmware use 115200.
- GPU metrics use NVIDIA NVML if available (requires NVIDIA GPU + drivers). If unavailable, GPU% and GPU_TEMP_C are reported as `-1` and shown as `N/A` on the device.

## Troubleshooting

- If the screen stays on the splash, ensure the Python sender is running and the COM port is correct.
- If auto-detect picks the wrong port, specify `--port COMx` explicitly.
- If no temperature is shown, run OpenHardwareMonitor or LibreHardwareMonitor to expose WMI sensors, or accept `N/A`.
- Close the PlatformIO Serial Monitor before running the Python sender. Only one program can open the COM port at a time.
- Find the correct port first:

```powershell
python .\pc\pc_stats_sender.py --list-ports
```

- Run with verbose output to confirm lines are sent:

```powershell
python .\pc\pc_stats_sender.py --port COM4 --verbose
```

- If the Wio still shows "Waiting for data...", try pressing the reset button on the Wio Terminal while the Python sender is running. Some USB CDC stacks deliver data only after a fresh open.

## Auto-run on Windows (at user logon)

You can set the sender to auto-run when you sign in (Scheduled Task):

1. From a PowerShell prompt in the repo root:

```powershell
# Basic install (auto-detect port, 0.5s interval)
.\scripts\install_autorun.ps1

# Or set a specific port and enable verbose
.\scripts\install_autorun.ps1 -Port COM4 -Interval 0.5 -VerboseMode
```

This will:
- Create a Python venv and install requirements
- Register a Scheduled Task named "Wio PC Monitor Sender" that runs at user logon

To remove it:

```powershell
.\scripts\uninstall_autorun.ps1
```
