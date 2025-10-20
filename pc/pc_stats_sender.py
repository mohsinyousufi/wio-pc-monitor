import argparse
import sys
import time
import platform
import atexit
from typing import Optional, Tuple

import psutil
import serial
import serial.tools.list_ports
import asyncio
from typing import Callable
try:
    from bleak import BleakClient
    BLEAK_AVAILABLE = True
except Exception:
    BLEAK_AVAILABLE = False

# On Windows, use WMI for temperatures if available
IS_WINDOWS = platform.system() == "Windows"
if IS_WINDOWS:
    # defer importing wmi to avoid initializing pywin32/pythoncom at module import time
    wmi = None  # type: ignore
else:
    wmi = None  # type: ignore

BAUD_RATE = 115200
SEND_INTERVAL_SEC = 0.5


"""Lightweight, resilient metrics sender.

Changes:
- Optional file logging (--log-file)
- Configurable serial open wait (--open-wait, default 0.5s)
- Cache LibreHardwareMonitor WMI object across reads
- Initialize NVML once and reuse handle instead of per-iteration init
"""

# Try to load NVIDIA NVML for GPU metrics
try:
    import pynvml  # type: ignore
    NVML_AVAILABLE = True
except Exception:
    NVML_AVAILABLE = False

# Globals for cached providers
LHM_WMI = None  # LibreHardwareMonitor WMI connection (if any)
NVML_INIT = False
NVML_HANDLE = None

# Try to connect to LibreHardwareMonitor WMI
def get_lhm_wmi():
    global LHM_WMI
    if LHM_WMI is not None:
        return LHM_WMI
    if not IS_WINDOWS or wmi is None:
        # Try importing wmi only when we need it
        try:
            import wmi as _wmi  # type: ignore
        except Exception:
            return None
        try:
            LHM_WMI = _wmi.WMI(namespace='root\\LibreHardwareMonitor')
            return LHM_WMI
        except Exception:
            LHM_WMI = None
            return None


def list_ports() -> list[serial.tools.list_ports_common.ListPortInfo]:
    return list(serial.tools.list_ports.comports())


def find_serial_port(preferred: Optional[str] = None) -> Optional[str]:
    if preferred:
        return preferred
    # Try to auto-detect by common VID/PID/names
    ports = list_ports()
    for p in ports:
        desc = (p.description or "").lower()
        hwid = (p.hwid or "").lower()
        if (
            "seeed" in desc
            or "wio" in desc
            or "seeeduino" in desc
            or "arduino" in desc
            or "usb serial" in desc
            or "cdc" in desc
        ):
            return p.device
    # Fallback to first available
    return ports[0].device if ports else None



def get_cpu_temp_c_lhm(lhm):
    """Get CPU temp from LibreHardwareMonitor WMI, or -1 if not found."""
    try:
        sensors = lhm.Sensor()
        cpu_temps = [float(s.Value) for s in sensors if s.SensorType == 'Temperature' and 'core max' in (s.Name or '').lower()]
        if cpu_temps:
            return cpu_temps[0]
    except Exception:
        pass
    return -1.0

def get_gpu_metrics_lhm(lhm):
    """Get (GPU usage %, GPU temp C) from LibreHardwareMonitor WMI, or (-1, -1) if not found."""
    try:
        sensors = lhm.Sensor()
        # Find first GPU
        
        gpu_loads = [float(s.Value) for s in sensors if s.SensorType == 'Load' and 'gpu core' in (s.Name or '').lower()]
        gpu_temps = [float(s.Value) for s in sensors if s.SensorType == 'Temperature' and 'gpu' in (s.Name or '').lower()]
        gpu_usage = gpu_loads[0] if gpu_loads else -1.0
        gpu_temp = gpu_temps[1] if gpu_temps else -1.0
        # return gpu_temp to 1 decimal place
        return gpu_usage, round(gpu_temp)
    except Exception:
        return -1.0, -1.0



def init_nvml_once() -> None:
    """Initialize NVML once and cache first device handle."""
    global NVML_INIT, NVML_HANDLE
    if NVML_INIT or not NVML_AVAILABLE:
        return
    try:
        pynvml.nvmlInit()
        if pynvml.nvmlDeviceGetCount() > 0:
            NVML_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
        NVML_INIT = True
        atexit.register(lambda: _nvml_shutdown_safe())
    except Exception:
        NVML_INIT = False
        NVML_HANDLE = None


def _nvml_shutdown_safe() -> None:
    try:
        if NVML_AVAILABLE:
            pynvml.nvmlShutdown()
    except Exception:
        pass


def get_gpu_metrics_nvml() -> Tuple[float, float]:
    """Return (gpu_usage_percent, gpu_temp_c) using NVML. -1, -1 if unavailable."""
    if not NVML_AVAILABLE:
        return -1.0, -1.0
    if not NVML_INIT:
        init_nvml_once()
    if NVML_HANDLE is None:
        return -1.0, -1.0
    try:
        util = pynvml.nvmlDeviceGetUtilizationRates(NVML_HANDLE)
        temp = pynvml.nvmlDeviceGetTemperature(NVML_HANDLE, pynvml.NVML_TEMPERATURE_GPU)
        return float(util.gpu), float(temp)
    except Exception:
        return -1.0, -1.0


def get_metrics() -> tuple[float, float, float, float, float]:
    cpu = float(psutil.cpu_percent(interval=None))
    ram = float(psutil.virtual_memory().percent)
    lhm = get_lhm_wmi()
    if lhm:
        temp_c = get_cpu_temp_c_lhm(lhm)
        gpu_usage, gpu_temp = get_gpu_metrics_lhm(lhm)
        debug_src = 'LHM'
    else:
        temp_c = -1.0
        gpu_usage, gpu_temp = get_gpu_metrics_nvml()
        debug_src = 'NVML' if NVML_AVAILABLE else 'none'
    # Debug print
    #print(f"[debug] src={debug_src} cpu={cpu:.1f} temp={temp_c:.1f} ram={ram:.1f} gpu={gpu_usage:.1f} gputemp={gpu_temp:.1f}")
    return cpu, temp_c, ram, gpu_usage, gpu_temp


def open_serial(port: str, open_wait: float) -> Optional[serial.Serial]:
    try:
        ser = serial.Serial(port=port, baudrate=BAUD_RATE, timeout=1)
        # Give device a brief moment to reset (configurable)
        time.sleep(max(0.0, float(open_wait)))
        return ser
    except Exception as e:
        log_print(f"[warn] Could not open {port}: {e}")
        return None


LOG_FILE = None  # type: Optional[str]


def log_print(msg: str) -> None:
    try:
        print(msg)
    except Exception:
        pass
    if LOG_FILE:
        try:
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(time.strftime('%Y-%m-%d %H:%M:%S') + ' ' + msg + '\n')
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Send PC stats to Wio Terminal over serial")
    parser.add_argument("--port", help="COM port (e.g., COM4). If omitted, try auto-detect.")
    parser.add_argument("--interval", type=float, default=SEND_INTERVAL_SEC, help="Send interval seconds (default 0.5s)")
    parser.add_argument("--dry-run", action="store_true", help="Print metrics without opening serial")
    parser.add_argument("--list-ports", action="store_true", help="List available serial ports and exit")
    parser.add_argument("--verbose", action="store_true", help="Print each line sent")
    parser.add_argument("--ble-address", help="BLE peripheral address to connect to (e.g., AA:BB:CC:DD:EE:FF). If provided, sender will attempt BLE write to UART RX char instead of serial.")
    parser.add_argument("--log-file", help="Append logs to this file (optional)")
    parser.add_argument("--open-wait", type=float, default=0.5, help="Seconds to wait after opening serial (default 0.5)")
    parser.add_argument("--open-retries", type=int, default=20, help="Retries to open/find serial before exit (default 20)")
    args = parser.parse_args()

    global LOG_FILE
    LOG_FILE = args.log_file or None

    if args.list_ports:
        ports = list_ports()
        if not ports:
            log_print("No serial ports found.")
            return 0
        log_print("Available serial ports:")
        for p in ports:
            log_print(f"- {p.device}: {p.description}")
        return 0

    ser = None
    ble_client = None
    ble_address = args.ble_address
    BLE_UART_RX_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
    if ble_address and not BLEAK_AVAILABLE:
        log_print("[error] bleak not installed; --ble-address cannot be used. Install with pip install bleak")
        return 3
    selected_port: Optional[str] = None

    # If BLE address provided, operate in BLE-only mode and skip serial port open
    if not args.dry_run and not ble_address:
        attempts = 0
        while attempts < max(1, int(args.open_retries)) and ser is None:
            selected_port = find_serial_port(args.port)
            if not selected_port:
                log_print(f"[warn] No serial port found (attempt {attempts+1}). Retrying...")
                attempts += 1
                time.sleep(1.0)
                continue
            ser = open_serial(selected_port, args.open_wait)
            if not ser:
                log_print(f"[warn] Open failed for {selected_port} (attempt {attempts+1}). Retrying...")
                attempts += 1
                time.sleep(1.0)
        if not ser:
            log_print("[error] Could not open serial port after retries. Exiting.")
            return 2
        # Show a quick summary of ports and selection
        ports = list_ports()
        if ports:
            log_print("[info] Ports detected:")
            for p in ports:
                log_print(f"  - {p.device}: {p.description}")
        log_print(f"[info] Using port: {selected_port} @ {BAUD_RATE}")

    # If BLE mode requested, connect once and keep client
    if ble_address:
        # Synchronous wrapper around bleak client connect/write
        async def ble_connect(address: str):
            client = BleakClient(address)
            await client.connect()
            return client

        try:
            ble_client = asyncio.run(ble_connect(ble_address))
            log_print(f"[info] Connected to BLE {ble_address}")
        except Exception as e:
            log_print(f"[error] BLE connect failed: {e}")
            ble_client = None

    try:
        while True:
            cpu, temp_c, ram, gpu_usage, gpu_temp = get_metrics()
            # CSV format: CPU%,CPU_TEMP_C,RAM%,GPU%,GPU_TEMP_C\n
            line = f"{cpu:.1f},{temp_c:.1f},{ram:.1f},{gpu_usage:.1f},{gpu_temp:.1f}\n"
            if args.dry_run:
                log_print(line.strip())
            else:
                if ble_address and ble_client:
                    # BLE write to UART RX characteristic (peripheral expects this)
                    try:
                        async def _ble_write(client: BleakClient, uuid: str, data: bytes):
                            await client.write_gatt_char(uuid, data)

                        asyncio.run(_ble_write(ble_client, BLE_UART_RX_UUID, line.encode('utf-8')))
                        if args.verbose:
                            log_print(f"[ble] {line.strip()}")
                    except Exception as e:
                        log_print(f"[warn] BLE write failed: {e}")
                        # try reconnect
                        try:
                            if ble_client and ble_client.is_connected:
                                asyncio.run(ble_client.disconnect())
                        except Exception:
                            pass
                        try:
                            ble_client = asyncio.run(ble_connect(ble_address))
                        except Exception as e:
                            log_print(f"[warn] BLE reconnect failed: {e}")
                else:
                    try:
                        ser.write(line.encode('utf-8'))
                        if args.verbose:
                            log_print(line.strip())
                    except Exception as e:
                        log_print(f"[warn] Write failed: {e}")
                        # Attempt simple reconnect once
                        try:
                            if ser:
                                ser.close()
                            time.sleep(0.5)
                            ser = open_serial(selected_port, args.open_wait)
                        except Exception:
                            pass
            time.sleep(max(0.05, float(args.interval)))
    except KeyboardInterrupt:
        log_print("\n[info] Stopped by user")
    finally:
        try:
            if ser:
                ser.close()
            if ble_client:
                try:
                    asyncio.run(ble_client.disconnect())
                except Exception:
                    pass
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
