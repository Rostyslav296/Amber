#!/usr/bin/env bash
set -euo pipefail

APP_NAME="AgentF"
APP_PATH="dist/${APP_NAME}.app"
VOL_NAME="${APP_NAME}"
OUT_DIR="dist"
DMG_NAME="${APP_NAME}-macOS.dmg"

# Optional: embed version in DMG name (if CFBundleShortVersionString present)
if /usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "${APP_PATH}/Contents/Info.plist" >/dev/null 2>&1; then
  VER=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "${APP_PATH}/Contents/Info.plist")
  DMG_NAME="${APP_NAME}-${VER}-macOS.dmg"
fi

# Check the app exists
if [[ ! -d "$APP_PATH" ]]; then
  echo "❌ ${APP_PATH} not found. Build the app first."
  exit 1
fi

# Stage a DMG root with the app + Applications symlink
STAGE="build/dmgroot"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$APP_PATH" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

# Create compressed DMG
mkdir -p "$OUT_DIR"
hdiutil create \
  -volname "$VOL_NAME" \
  -srcfolder "$STAGE" \
  -ov -format UDZO -imagekey zlib-level=9 \
  "${OUT_DIR}/${DMG_NAME}"

echo "✅ DMG created: ${OUT_DIR}/${DMG_NAME}"
echo "👉 Users can open the DMG and drag '${APP_NAME}.app' into Applications."

# Optional: SHA-256 for release notes
if command -v shasum >/dev/null; then
  echo "🔐 SHA-256:"
  shasum -a 256 "${OUT_DIR}/${DMG_NAME}"
fi

