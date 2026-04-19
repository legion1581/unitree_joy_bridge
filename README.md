# Unitree Joy Bridge

Turn a Unitree handheld remote into a standard Linux gamepad through BLE.

<p align="center">
  <img src="images/header.webp" alt="Unitree R3 handheld remote" width="520">
</p>

Bridges a Unitree BLE handheld remote to a standard Linux gamepad (`/dev/input/jsX`
and `/dev/input/eventX`), so any SDL2 / evdev / ROS `joy` / browser Gamepad-API
consumer sees it as a normal controller — no Unitree-specific integration needed.

## Supported hardware

- Unitree R3 handheld remote
- Older Unitree handheld remote (not sure about the model name)

The script discovers any BLE device whose advertised name starts with `Unitree`.

## What you get

A virtual gamepad with:
- **Sticks**: `ABS_X`, `ABS_Y`, `ABS_RX`, `ABS_RY` (`-32767..+32767`, flat zone 128)
- **D-pad**: `ABS_HAT0X`, `ABS_HAT0Y`
- **Buttons**: A / B / X / Y, L1 / R1 / L2 / R2, Select / Start, F1 (MODE) / F2 (THUMBL)

## Install

```bash
# one-time: make /dev/uinput accessible without sudo
./install.sh

# one-time: allow pygatt's HCI helpers to run as your user
sudo setcap 'cap_net_raw,cap_net_admin+eip' "$(which gatttool)"
sudo setcap 'cap_net_raw,cap_net_admin+eip' "$(which hcitool)"

# python deps (user install)
pip install -r requirements.txt
```

Log out and back in once after the first run of `install.sh` (group change).

## Run

```bash
# auto-scan for any Unitree remote
python3 unitree_joy_bridge.py -v

# or connect directly if you know the MAC
python3 unitree_joy_bridge.py -i hci1 -a AA:BB:CC:DD:EE:FF -v
```

Options:
- `-i / --hci` — HCI adapter (default `hci0`)
- `-a / --address` — remote MAC (skips scan)
- `-v / --verbose` — print decoded state
- `--scan-timeout` — BLE scan duration (default 8 s)

## Test

```bash
evtest /dev/input/event<N>
jstest /dev/input/js<N>
jstest-gtk                  # GUI
```

The startup line prints the exact paths created, e.g.:

```
Connected. Virtual gamepad 'Unitree BLE Remote (bridge)' → /dev/input/event21 + /dev/input/js1
```

## Notes

- If the first connect attempt times out, the script retries automatically. A brief
  bleak scan is run before each connect to warm up BlueZ's LE device cache.
- The remote advertises at low duty cycle; give scans a few seconds.
- The script exits cleanly if no BLE notifications arrive for 2 s (remote powered
  off or out of range).

## Requirements

- Linux with BlueZ
- Python 3.10+
- `pygatt`, `evdev`, `bleak` (see `requirements.txt`)
- `bluez` package (provides `gatttool`, `hcitool`, `bluetoothctl`)
