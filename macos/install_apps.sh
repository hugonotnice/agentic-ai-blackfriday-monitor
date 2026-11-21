#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_URL="${PRICEBUDDY_URL:-http://127.0.0.1:8080}"
ARCH="$(uname -m)"

echo "Installing /Applications/PriceBuddy.app..."
rm -rf "/Applications/PriceBuddy.app"
mkdir -p "/Applications/PriceBuddy.app/Contents/MacOS" "/Applications/PriceBuddy.app/Contents/Resources"
cp "$SCRIPT_DIR/PriceBuddy.icns" "/Applications/PriceBuddy.app/Contents/Resources/PriceBuddy.icns"

cat << 'EOF' > "/Applications/PriceBuddy.app/Contents/Info.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>PriceBuddy</string>
    <key>CFBundleIconFile</key>
    <string>PriceBuddy</string>
    <key>CFBundleIdentifier</key>
    <string>com.pricebuddy.localapp</string>
    <key>CFBundleName</key>
    <string>PriceBuddy</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
EOF

cat << EOF > "/Applications/PriceBuddy.app/Contents/MacOS/PriceBuddy"
#!/bin/bash
exec "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \\
  --app="${APP_URL}/admin" \\
  --profile-directory="Default" \\
  "\$@" >/dev/null 2>&1 &
EOF
chmod +x "/Applications/PriceBuddy.app/Contents/MacOS/PriceBuddy"

echo "Compiling /Applications/PriceBuddy Bar.app (${ARCH})..."
rm -rf "/Applications/PriceBuddy Bar.app"
mkdir -p "/Applications/PriceBuddy Bar.app/Contents/MacOS" "/Applications/PriceBuddy Bar.app/Contents/Resources"
cp "$SCRIPT_DIR/PriceBuddy.icns" "/Applications/PriceBuddy Bar.app/Contents/Resources/PriceBuddy.icns"

clang -O2 -fobjc-arc -arch "$ARCH" \
  -o "/Applications/PriceBuddy Bar.app/Contents/MacOS/PriceBuddyBar" \
  "$SCRIPT_DIR/PriceBuddyBar.m" \
  -framework Cocoa

cat << 'EOF' > "/Applications/PriceBuddy Bar.app/Contents/Info.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>PriceBuddyBar</string>
    <key>CFBundleIconFile</key>
    <string>PriceBuddy</string>
    <key>CFBundleIdentifier</key>
    <string>com.pricebuddy.menubar</string>
    <key>CFBundleName</key>
    <string>PriceBuddy Bar</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
EOF

codesign --force --deep --sign - "/Applications/PriceBuddy.app" >/dev/null 2>&1 || true
codesign --force --deep --sign - "/Applications/PriceBuddy Bar.app" >/dev/null 2>&1 || true

open "/Applications/PriceBuddy Bar.app"
echo "Done. PriceBuddy Bar is running in your macOS menu bar."
echo "Tip: copy $REPO_ROOT/harness.env.example to ~/.config/agentic-blackfriday-monitor/harness.env if you run from outside ~/blackfriday-monitor."
