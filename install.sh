#!/bin/sh
# Add Nick's Badge to the app menu, and let your user talk to the badge over USB.
set -e
cd "$(dirname "$0")"
APPDIR=$(pwd)
DATA=${XDG_DATA_HOME:-$HOME/.local/share}

mkdir -p "$DATA/applications" "$DATA/icons/hicolor/scalable/apps"
sed "s|@APPDIR@|$APPDIR|" data/dev.nick.Nametag.desktop.in > "$DATA/applications/dev.nick.Nametag.desktop"
cp data/dev.nick.Nametag.svg "$DATA/icons/hicolor/scalable/apps/"
echo "Added Nick's Badge to the app menu."

if [ ! -f /etc/udev/rules.d/70-led-badge.rules ]; then
    echo "Installing the USB permission rule (asks for your password)..."
    sudo cp 70-led-badge.rules /etc/udev/rules.d/
    sudo udevadm control --reload && sudo udevadm trigger
    echo "Done. Unplug and replug the badge."
fi
