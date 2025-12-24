import argparse
import sys
import time
import platform
import atexit
from typing import Optional, Tuple, Any, Dict
from bleak import BleakScanner

import psutil
import requests
import re
import asyncio
try:
    from bleak import BleakClient
    BLEAK_AVAILABLE = True
except Exception:
    BLEAK_AVAILABLE = False

IS_WINDOWS = platform.system() == "Windows"
SEND_INTERVAL_SEC = 0.5


"""Lightweight, resilient metrics sender (BLE-only).

Changes:
- Optional file logging (--log-file)
- Uses LibreHardwareMonitor RemoteWebServer JSON or NVML for GPU
- Initialize NVML once and reuse handle instead of per-iteration init
"""

# Try to load NVIDIA NVML for GPU metrics
try:
    import pynvml  # type: ignore
    NVML_AVAILABLE = True
except Exception:
    NVML_AVAILABLE = False

# Globals for cached providers
NVML_INIT = False
NVML_HANDLE = None


# --- LibreHardwareMonitor Remote Web Server JSON API ---
LHM_REMOTE_URL = "http://localhost:8085/data.json"  # Change port if needed

def fetch_lhm_json():
    try:
        resp = requests.get(LHM_REMOTE_URL, timeout=2)
        if resp.status_code == 200:
            data = resp.json()
            try:
                # Print a short summary for debugging
                top_children = data.get('Children', [])
                print(f"[debug] LHM JSON fetched: {len(top_children)} top children")
                for entry in top_children:
                    txt = entry.get('Text') or entry.get('Name') or ''
                    print(f"  - section: {txt}")
                # Also search for key sensor ids and print matches
                def _search(node, path):
                    if isinstance(node, dict):
                        if 'id' in node and node.get('id') in (20, 221, 224):
                            print(f"[debug] MATCH id={node.get('id')} path={'/'.join(path)} Value={node.get('Value')}")
                        for k, v in node.items():
                            if isinstance(v, (dict, list)):
                                _search(v, path + [str(node.get('Text') or node.get('Name') or k)])
                    elif isinstance(node, list):
                        for i, item in enumerate(node):
                            _search(item, path + [f'[{i}]'])
                try:
                    _search(data, ['root'])
                except Exception:
                    pass
            except Exception:
                pass
            return data
    except Exception:
        pass
    return None


# --- Helpers to index and parse sensors ---
_UNIT_RE = re.compile(r'([-+]?[0-9]*\.?[0-9]+)')

def _parse_numeric(val: Any) -> float:
    if val is None:
        return -1.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    m = _UNIT_RE.search(s)
    if m:
        try:
            return float(m.group(1))
        except Exception:
            return -1.0
    return -1.0


def build_sensor_index(tree: Any, out: Dict[int, Dict] | None = None) -> Dict[int, Dict]:
    if out is None:
        out = {}
    if isinstance(tree, dict):
        sid = tree.get('id')
        if isinstance(sid, int):
            out[sid] = tree
        for v in tree.values():
            if isinstance(v, (dict, list)):
                build_sensor_index(v, out)
    elif isinstance(tree, list):
        for item in tree:
            if isinstance(item, (dict, list)):
                build_sensor_index(item, out)
    return out

def get_cpu_temp_c_lhm_json(data):
    """Get CPU temp from LHM JSON, or -1 if not found."""
    try:
        idx = build_sensor_index(data)
        sensor = idx.get(20)
        if sensor is None:
            return -1.0
        val = sensor.get('Value', '')
        print(f"[debug] CPU sensor id=20 raw Value={val}")
        return _parse_numeric(val)
    except Exception:
        return -1.0

def get_gpu_metrics_lhm_json(data):
    """Get (GPU usage %, GPU temp C) from LHM JSON, or (-1, -1) if not found."""
    try:
        idx = build_sensor_index(data)
        gpu_temp = _parse_numeric(idx.get(221, {}).get('Value'))
        gpu_load = _parse_numeric(idx.get(224, {}).get('Value'))
        print(f"[debug] GPU sensors raw temp={idx.get(221, {}).get('Value')} load={idx.get(224, {}).get('Value')}")
        return gpu_load, gpu_temp
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



# --- Metrics collection function ---
def get_metrics() -> tuple[float, float, float, float, float]:
    cpu = float(psutil.cpu_percent(interval=None))
    ram = float(psutil.virtual_memory().percent)
    lhm_data = fetch_lhm_json()
    if lhm_data:
        temp_c = get_cpu_temp_c_lhm_json(lhm_data)
        gpu_usage, gpu_temp = get_gpu_metrics_lhm_json(lhm_data)
        debug_src = 'LHM_JSON'
    else:
        temp_c = -1.0
        gpu_usage, gpu_temp = get_gpu_metrics_nvml()
        debug_src = 'NVML' if NVML_AVAILABLE else 'none'
    # Debug print
    #print(f"[debug] src={debug_src} cpu={cpu:.1f} temp={temp_c:.1f} ram={ram:.1f} gpu={gpu_usage:.1f} gputemp={gpu_temp:.1f}")
    return cpu, temp_c, ram, gpu_usage, gpu_temp


# serial port functions removed — BLE-only operation


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
    parser = argparse.ArgumentParser(description="Send PC stats to Wio Terminal over BLE")
    parser.add_argument("--interval", type=float, default=SEND_INTERVAL_SEC, help="Send interval seconds (default 0.5s)")
    parser.add_argument("--dry-run", action="store_true", help="Print metrics without sending")
    parser.add_argument("--verbose", action="store_true", help="Print each line sent")
    parser.add_argument("--ble-address", help="BLE peripheral address to connect to (e.g., AA:BB:CC:DD:EE:FF)")
    parser.add_argument("--log-file", help="Append logs to this file (optional)")
    args = parser.parse_args()

    global LOG_FILE
    LOG_FILE = args.log_file or None

    # (serial port listing removed; BLE-only mode)


    def find_wio_ble_address(target_name="WioMonitor", timeout=5.0):
        print(f"Scanning for BLE devices named '{target_name}'...")
        devices = asyncio.run(BleakScanner.discover(timeout=timeout))
        for d in devices:
            if d.name and target_name.lower() in d.name.lower():
                print(f"Found Wio Terminal: {d.name} [{d.address}]")
                return d.address
        print("Wio Terminal not found via BLE scan.")
        return None

    ble_client = None
    ble_address = args.ble_address
    if not ble_address:
        ble_address = find_wio_ble_address()
        if not ble_address:
            log_print("[error] Could not auto-detect Wio Terminal BLE address.")
            return 4
    BLE_UART_RX_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
    if not BLEAK_AVAILABLE:
        log_print("[error] bleak not installed; --ble-address cannot be used. Install with pip install bleak")
        return 3


    # BLE service scanner
    async def ble_service_scan(address: str):
        print(f"[debug] Scanning GATT services for {address}")
        try:
            async with BleakClient(address) as client:
                print(f"[debug] Connected: {client.is_connected}")
                # Try both Bleak APIs for service discovery
                services = None
                try:
                    # Newer Bleak: client.services (after connect)
                    services = client.services
                except Exception:
                    pass
                if not services:
                    try:
                        # Older Bleak: await client.get_services()
                        services = await client.get_services()
                    except Exception:
                        pass
                if not services:
                    print("[error] Could not retrieve GATT services (both APIs failed)")
                    return
                print(f"[debug] Services for {address}:")
                for service in services:
                    print(f"  Service: {service.uuid} | {getattr(service, 'description', '')}")
                    for char in service.characteristics:
                        print(f"    Characteristic: {char.uuid} | {getattr(char, 'description', '')} | Properties: {getattr(char, 'properties', '')}")
        except Exception as e:
            print(f"[error] Service scan failed: {e}")

    # BLE connect helper
    async def ble_connect(address: str):
        print(f"[debug] Attempting BLE connect to {address}")
        client = BleakClient(address)
        await client.connect()
        print(f"[debug] BLE connected: {client.is_connected}")
        return client


    # First, scan and print all services/characteristics for debugging
    print("[info] Running BLE service scan for debugging...")
    asyncio.run(ble_service_scan(ble_address))

    try:
        ble_client = asyncio.run(ble_connect(ble_address))
        log_print(f"[info] Connected to BLE {ble_address}")
    except Exception as e:
        log_print(f"[error] BLE connect failed: {e}")
        return 5

    try:
        while True:
            cpu, temp_c, ram, gpu_usage, gpu_temp = get_metrics()
            line = f"{cpu:.1f},{temp_c:.1f},{ram:.1f},{gpu_usage:.1f},{gpu_temp:.1f}\n"
            if args.dry_run:
                log_print(line.strip())
            else:
                # Print parsed metric values before sending
                log_print(f"[send] CPU%={cpu:.1f} CPU_TEMP_C={temp_c:.1f} RAM%={ram:.1f} GPU%={gpu_usage:.1f} GPU_TEMP_C={gpu_temp:.1f}")
                
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
            time.sleep(max(0.05, float(args.interval)))
    except KeyboardInterrupt:
        log_print("\n[info] Stopped by user")
    finally:
        try:
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
