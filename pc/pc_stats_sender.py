import argparse
import sys
import time
import platform
from typing import Optional

import psutil
import serial
import serial.tools.list_ports

# On Windows, use WMI for temperatures if available
IS_WINDOWS = platform.system() == "Windows"
if IS_WINDOWS:
    try:
        import wmi  # type: ignore
    except Exception:  # pragma: no cover
        wmi = None  # type: ignore
else:
    wmi = None  # type: ignore

BAUD_RATE = 115200
SEND_INTERVAL_SEC = 0.5


# Try to load NVIDIA NVML for GPU metrics
try:
    import pynvml  # type: ignore
    NVML_AVAILABLE = True
except Exception:
    NVML_AVAILABLE = False

# Try to connect to LibreHardwareMonitor WMI
def get_lhm_wmi():
    if not IS_WINDOWS or wmi is None:
        return None
    try:
        return wmi.WMI(namespace='root\\LibreHardwareMonitor')
    except Exception:
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



def get_gpu_metrics_nvml() -> tuple[float, float]:
    """Return (gpu_usage_percent, gpu_temp_c) using NVML. -1, -1 if unavailable."""
    if not NVML_AVAILABLE:
        return -1.0, -1.0
    try:
        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        if count < 1:
            pynvml.nvmlShutdown()
            return -1.0, -1.0
        # Use first GPU
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        gpu_usage = float(util.gpu)
        gpu_temp = float(temp)
        pynvml.nvmlShutdown()
        return gpu_usage, gpu_temp
    except Exception:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass
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


def open_serial(port: str) -> Optional[serial.Serial]:
    try:
        ser = serial.Serial(port=port, baudrate=BAUD_RATE, timeout=1)
        # Give device a moment to reset on connect (typical for Arduino-like boards)
        time.sleep(2.0)
        return ser
    except Exception as e:
        print(f"[warn] Could not open {port}: {e}")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Send PC stats to Wio Terminal over serial")
    parser.add_argument("--port", help="COM port (e.g., COM4). If omitted, try auto-detect.")
    parser.add_argument("--interval", type=float, default=SEND_INTERVAL_SEC, help="Send interval seconds (default 0.5s)")
    parser.add_argument("--dry-run", action="store_true", help="Print metrics without opening serial")
    parser.add_argument("--list-ports", action="store_true", help="List available serial ports and exit")
    parser.add_argument("--verbose", action="store_true", help="Print each line sent")
    args = parser.parse_args()

    if args.list_ports:
        ports = list_ports()
        if not ports:
            print("No serial ports found.")
            return 0
        print("Available serial ports:")
        for p in ports:
            print(f"- {p.device}: {p.description}")
        return 0

    port = find_serial_port(args.port)
    if not args.dry_run and not port:
        print("[error] No serial port found. Specify --port COMx or plug in the Wio Terminal.")
        return 2

    ser = None
    if not args.dry_run:
        ser = open_serial(port)
        if not ser:
            return 2
        # Show a quick summary of ports and selection
        ports = list_ports()
        if ports:
            print("[info] Ports detected:")
            for p in ports:
                print(f"  - {p.device}: {p.description}")
        print(f"[info] Using port: {port} @ {BAUD_RATE}")

    try:
        while True:
            cpu, temp_c, ram, gpu_usage, gpu_temp = get_metrics()
            # CSV format: CPU%,CPU_TEMP_C,RAM%,GPU%,GPU_TEMP_C\n
            line = f"{cpu:.1f},{temp_c:.1f},{ram:.1f},{gpu_usage:.1f},{gpu_temp:.1f}\n"
            if args.dry_run:
                print(line.strip())
            else:
                try:
                    ser.write(line.encode('utf-8'))
                    if args.verbose:
                        print(line.strip())
                except Exception as e:
                    print(f"[warn] Write failed: {e}")
                    # Attempt simple reconnect once
                    try:
                        if ser:
                            ser.close()
                        time.sleep(1.0)
                        ser = open_serial(port)
                    except Exception:
                        pass
            time.sleep(max(0.05, float(args.interval)))
    except KeyboardInterrupt:
        print("\n[info] Stopped by user")
    finally:
        try:
            if ser:
                ser.close()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
