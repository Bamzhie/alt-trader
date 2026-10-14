#!/usr/bin/env bash
set -euo pipefail

app_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
binary_source="$app_root/src-tauri/target/release/alt-radar-desktop"
icon_source="$app_root/src-tauri/icons/128x128.png"

if [[ ! -x "$binary_source" || ! -f "$icon_source" ]]; then
  printf 'Build the native app first: cd app && npm run tauri -- build --bundles deb\n' >&2
  exit 1
fi

app_dir="$HOME/.local/opt/alt-radar"
binary_target="$app_dir/alt-radar-desktop"
icon_dir="$HOME/.local/share/icons/hicolor/128x128/apps"
desktop_dir="$HOME/.local/share/applications"

install -d "$app_dir" "$icon_dir" "$desktop_dir"
install -m 755 "$binary_source" "$binary_target"
install -m 644 "$icon_source" "$icon_dir/alt-radar-desktop.png"

desktop_file="$desktop_dir/alt-radar.desktop"
cat >"$desktop_file" <<EOF
[Desktop Entry]
Type=Application
Name=ALT RADAR
GenericName=ALT Trader
Comment=ALT RADAR perpetual futures research dashboard
Keywords=ALT Trader;perpetual futures;scanner;crypto;
Exec=$binary_target
TryExec=$binary_target
Icon=alt-radar-desktop
Terminal=false
Categories=Office;Finance;
StartupWMClass=alt-radar-desktop
EOF

chmod 755 "$desktop_file"
if [[ -d "$HOME/Desktop" ]]; then
  install -m 755 "$desktop_file" "$HOME/Desktop/ALT-RADAR.desktop"
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$desktop_dir" >/dev/null 2>&1 || true
fi

printf 'Installed ALT RADAR desktop launcher: %s\n' "$desktop_file"
printf 'Backend remains managed separately by systemd: altradar.service\n'
