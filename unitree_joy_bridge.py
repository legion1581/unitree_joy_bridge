#!/usr/bin/env python3
"""Bridge a Unitree BLE remote to a Linux uinput gamepad (/dev/input/jsX, /dev/input/eventX)."""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import struct
import subprocess
import sys
import time
from typing import Optional

import pygatt
from evdev import UInput, AbsInfo, ecodes as e

SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000ffe2-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

HANDSHAKE = "".join(f"{ord(c):x}" for c in "YS+2").encode("utf-8")  # b"59532b32"

BUTTON_NAMES = [
    "R1", "L1", "Start", "Select", "R2", "L2", "F1", "F2",
    "A", "B", "X", "Y", "Up", "Right", "Down", "Left",
]

BUTTON_KEYMAP = {
    "A": e.BTN_SOUTH,
    "B": e.BTN_EAST,
    "X": e.BTN_NORTH,
    "Y": e.BTN_WEST,
    "L1": e.BTN_TL,
    "R1": e.BTN_TR,
    "L2": e.BTN_TL2,
    "R2": e.BTN_TR2,
    "Select": e.BTN_SELECT,
    "Start": e.BTN_START,
    "F1": e.BTN_MODE,
    "F2": e.BTN_THUMBL,
}

AXIS_MAX = 32767
STALE_TIMEOUT = 2.0


def build_uinput() -> UInput:
    axis_info = AbsInfo(value=0, min=-AXIS_MAX, max=AXIS_MAX, fuzz=16, flat=128, resolution=0)
    hat_info = AbsInfo(value=0, min=-1, max=1, fuzz=0, flat=0, resolution=0)
    capabilities = {
        e.EV_KEY: sorted(set(BUTTON_KEYMAP.values())),
        e.EV_ABS: [
            (e.ABS_X, axis_info),
            (e.ABS_Y, axis_info),
            (e.ABS_RX, axis_info),
            (e.ABS_RY, axis_info),
            (e.ABS_HAT0X, hat_info),
            (e.ABS_HAT0Y, hat_info),
        ],
    }
    return UInput(
        events=capabilities,
        name="Unitree BLE Remote (bridge)",
        vendor=0x1D6B,
        product=0x0002,
        version=0x0001,
    )


def resolve_js_path(event_path: str, retries: int = 10, delay: float = 0.05) -> Optional[str]:
    """Given /dev/input/eventN, find the sibling /dev/input/jsM created by joydev."""
    event_name = os.path.basename(event_path)  # "eventN"
    sys_event = f"/sys/class/input/{event_name}/device"
    for _ in range(retries):
        try:
            for entry in os.listdir(sys_event):
                if entry.startswith("js") and entry[2:].isdigit():
                    return f"/dev/input/{entry}"
        except FileNotFoundError:
            pass
        time.sleep(delay)
    return None


def axis_to_int(v: float) -> int:
    v = max(-1.0, min(1.0, v))
    return int(round(v * AXIS_MAX))


class Bridge:
    def __init__(self, ui: UInput, verbose: bool = False):
        self.ui = ui
        self.verbose = verbose
        self.verbose_ready = False
        self.prev_buttons: dict[str, bool] = {name: False for name in BUTTON_NAMES}
        self.prev_axes = {"lx": 0, "ly": 0, "rx": 0, "ry": 0}
        self.prev_hat = (0, 0)
        self.last_notify = time.monotonic()

    def on_packet(self, data: bytes) -> None:
        if len(data) < 20:
            return
        self.last_notify = time.monotonic()

        lx = struct.unpack_from("<f", data, 0)[0]
        rx = struct.unpack_from("<f", data, 4)[0]
        ry = struct.unpack_from("<f", data, 8)[0]
        ly = struct.unpack_from("<f", data, 12)[0]
        btn1 = data[16]
        btn2 = data[17]

        buttons: dict[str, bool] = {}
        for i, name in enumerate(BUTTON_NAMES):
            byte = btn1 if i < 8 else btn2
            bit = i if i < 8 else i - 8
            buttons[name] = bool((byte >> bit) & 1)

        # Axes. Note: ly inverted so "up" -> negative (standard gamepad convention).
        ax = {
            "lx": axis_to_int(lx),
            "ly": axis_to_int(-ly),
            "rx": axis_to_int(rx),
            "ry": axis_to_int(-ry),
        }
        abs_map = {"lx": e.ABS_X, "ly": e.ABS_Y, "rx": e.ABS_RX, "ry": e.ABS_RY}
        changed = False
        for k, code in abs_map.items():
            if ax[k] != self.prev_axes[k]:
                self.ui.write(e.EV_ABS, code, ax[k])
                self.prev_axes[k] = ax[k]
                changed = True

        hat_x = (1 if buttons["Right"] else 0) - (1 if buttons["Left"] else 0)
        hat_y = (1 if buttons["Down"] else 0) - (1 if buttons["Up"] else 0)
        if (hat_x, hat_y) != self.prev_hat:
            if hat_x != self.prev_hat[0]:
                self.ui.write(e.EV_ABS, e.ABS_HAT0X, hat_x)
            if hat_y != self.prev_hat[1]:
                self.ui.write(e.EV_ABS, e.ABS_HAT0Y, hat_y)
            self.prev_hat = (hat_x, hat_y)
            changed = True

        for name, code in BUTTON_KEYMAP.items():
            if buttons[name] != self.prev_buttons[name]:
                self.ui.write(e.EV_KEY, code, 1 if buttons[name] else 0)
                self.prev_buttons[name] = buttons[name]
                changed = True

        if changed:
            self.ui.syn()

        if self.verbose and self.verbose_ready:
            pressed = [n for n, v in buttons.items() if v]
            sys.stdout.write(
                f"\rlx={lx:+.2f} ly={ly:+.2f} rx={rx:+.2f} ry={ry:+.2f} "
                f"bat={data[18]:3d}% rssi={(data[19] if data[19] < 128 else data[19]-256):+4d} "
                f"btn={','.join(pressed) or '-':<30}"
            )
            sys.stdout.flush()


async def scan_for_remote(timeout: float = 8.0, hci: str = "hci0") -> Optional[str]:
    try:
        from bleak import BleakScanner
    except ImportError:
        print("bleak not installed; cannot scan. Pass --address or pip install bleak.", file=sys.stderr)
        return None
    print(f"Scanning for Unitree remote on {hci} ({timeout:.0f}s)...", file=sys.stderr)
    devices = await BleakScanner.discover(timeout=timeout, adapter=hci)
    for d in devices:
        name = (d.name or "")
        if name.startswith("Unitree"):
            print(f"Found: {name} @ {d.address}", file=sys.stderr)
            return d.address
    return None


async def warmup_adapter(hci: str, timeout: float = 3.0) -> None:
    """
    Brief bleak scan to populate BlueZ's LE device cache on the adapter.
    gatttool's first LE-Create-Connection otherwise waits for BlueZ to
    organically see an advertisement, which often exceeds its own timeout.
    """
    try:
        from bleak import BleakScanner
    except ImportError:
        return
    try:
        await BleakScanner.discover(timeout=timeout, adapter=hci)
    except Exception:
        pass


def connect_remote(address: str, hci: str, bridge: Bridge, attempts: int = 3):
    last_err: Optional[Exception] = None
    adapter = None
    device = None

    for attempt in range(1, attempts + 1):
        # Clear stale BlueZ cache (bleak scan / prior pairing can block gatttool)
        try:
            subprocess.run(
                ["bluetoothctl", "remove", address],
                capture_output=True, timeout=3, check=False,
            )
        except Exception:
            pass

        adapter = pygatt.GATTToolBackend(hci_device=hci)
        adapter.start()
        try:
            device = adapter.connect(
                address,
                address_type=pygatt.BLEAddressType.public,
                timeout=15,
            )
            last_err = None
            break
        except Exception as ex:
            last_err = ex
            print(f"Attempt {attempt}/{attempts} failed: {ex}", file=sys.stderr)
            try:
                adapter.stop()
            except Exception:
                pass
            adapter = None
            device = None
            time.sleep(1.0)

    if last_err is not None or device is None:
        raise RuntimeError(f"Connect failed after {attempts} attempts: {last_err}")

    try:
        device.char_write(WRITE_UUID, HANDSHAKE, wait_for_response=False)
    except TypeError:
        device.char_write(WRITE_UUID, HANDSHAKE)
    except Exception as ex:
        print(f"Handshake write warning: {ex}", file=sys.stderr)

    device.subscribe(NOTIFY_UUID, callback=lambda handle, value: bridge.on_packet(bytes(value)))
    bridge.last_notify = time.monotonic()
    return device, adapter


def main() -> int:
    parser = argparse.ArgumentParser(description="Unitree BLE remote -> uinput gamepad bridge")
    parser.add_argument("--address", "-a", help="Remote MAC address (if omitted, scans for 'Unitree*')")
    parser.add_argument("--hci", "-i", default="hci0",
                        help="HCI adapter for both scan and connect (e.g. hci0, hci1). Default: hci0")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print decoded state to stdout")
    parser.add_argument("--scan-timeout", type=float, default=8.0)
    args = parser.parse_args()

    address = args.address
    if not address:
        address = asyncio.run(scan_for_remote(args.scan_timeout, args.hci))
        if not address:
            print("No Unitree remote found. Power it on and try again, or pass --address.", file=sys.stderr)
            return 2
    else:
        # Warm up BlueZ so gatttool's first connect attempt doesn't time out.
        print(f"Warming up {args.hci} (brief LE scan)...", file=sys.stderr)
        asyncio.run(warmup_adapter(args.hci))

    try:
        ui = build_uinput()
    except PermissionError:
        print("Permission denied opening /dev/uinput. Run with sudo, or:", file=sys.stderr)
        print("  sudo modprobe uinput && sudo chmod 660 /dev/uinput", file=sys.stderr)
        print("  sudo usermod -aG input $USER  (logout/login after)", file=sys.stderr)
        return 1

    bridge = Bridge(ui, verbose=args.verbose)

    stop = {"flag": False}

    def handle_sig(signum, frame):
        stop["flag"] = True

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    device = None
    adapter = None
    try:
        print(f"Connecting to {address} on {args.hci}...", file=sys.stderr)
        device, adapter = connect_remote(address, args.hci, bridge)
        event_path = ui.device.path
        js_path = resolve_js_path(event_path)
        paths = f"{event_path}" + (f" + {js_path}" if js_path else " (no jsX yet)")
        print(f"Connected. Virtual gamepad '{ui.device.name}' → {paths}", file=sys.stderr, flush=True)
        bridge.verbose_ready = True

        while not stop["flag"]:
            if (time.monotonic() - bridge.last_notify) > STALE_TIMEOUT:
                print("\nRemote went stale (no data for >2s). Exiting.", file=sys.stderr)
                break
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    except Exception as ex:
        print(f"\nError: {ex}", file=sys.stderr)
        return 1
    finally:
        if device is not None:
            try:
                device.disconnect()
            except Exception:
                pass
        if adapter is not None:
            try:
                adapter.stop()
            except Exception:
                pass
        try:
            ui.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
