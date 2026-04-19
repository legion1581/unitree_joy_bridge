#!/usr/bin/env bash
# One-shot installer: grants the current user permission to create a virtual
# gamepad via /dev/uinput without sudo at runtime.
#
# Re-runnable; only changes what's missing.

set -euo pipefail

if [[ $EUID -eq 0 ]]; then
    echo "Run as your normal user, not root. The script will call sudo itself." >&2
    exit 1
fi

TARGET_USER="${SUDO_USER:-$USER}"
MODULES_CONF="/etc/modules-load.d/uinput.conf"
UDEV_RULE="/etc/udev/rules.d/99-uinput.rules"
UDEV_LINE='KERNEL=="uinput", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"'

echo ">> Ensuring uinput module loads at boot..."
if ! grep -qx "uinput" "$MODULES_CONF" 2>/dev/null; then
    echo "uinput" | sudo tee "$MODULES_CONF" >/dev/null
    echo "   wrote $MODULES_CONF"
else
    echo "   already present in $MODULES_CONF"
fi

echo ">> Ensuring uinput udev rule..."
if ! sudo test -f "$UDEV_RULE" || ! sudo grep -qF "$UDEV_LINE" "$UDEV_RULE"; then
    echo "$UDEV_LINE" | sudo tee "$UDEV_RULE" >/dev/null
    echo "   wrote $UDEV_RULE"
else
    echo "   already present in $UDEV_RULE"
fi

echo ">> Loading uinput now..."
sudo modprobe uinput

echo ">> Reloading udev..."
sudo udevadm control --reload-rules
sudo udevadm trigger

echo ">> Ensuring $TARGET_USER is in the 'input' group..."
if id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx "input"; then
    echo "   already a member"
    NEED_RELOGIN=0
else
    sudo usermod -aG input "$TARGET_USER"
    echo "   added to 'input'"
    NEED_RELOGIN=1
fi

echo
echo "Done."
if [[ "$NEED_RELOGIN" -eq 1 ]]; then
    echo
    echo "⚠  Group membership changed. Log out and back in (or reboot) before running"
    echo "   the bridge as a normal user."
fi
echo
echo "Verify:"
echo "   ls -l /dev/uinput       # should show group 'input' and mode 0660"
echo "   id | tr ',' '\\n' | grep input"
