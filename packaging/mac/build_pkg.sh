#!/bin/bash
# Build dist/VEGAAnnotator-<version>.pkg, the Mac installer. It holds the app and its source
# code; the app downloads Python and PyTorch when it first opens, and copies SAM3 from the
# server folder. This follows Sponge Lab's installer.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERSION="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from vega_annotator import VERSION; print(VERSION)' "$ROOT")"
APP_NAME="VEGA Annotator"
IDENTIFIER="org.vicar.vega-annotator"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
APP="$WORK/root/$APP_NAME.app"
OUT="$ROOT/dist/VEGAAnnotator-$VERSION.pkg"

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app" "$ROOT/dist"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundleIdentifier</key><string>$IDENTIFIER</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launch</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
</dict>
</plist>
PLIST
cp "$ROOT/packaging/mac/launch.sh" "$APP/Contents/MacOS/launch"
cp "$ROOT/packaging/mac/setup.sh" "$APP/Contents/Resources/setup.sh"
chmod +x "$APP/Contents/MacOS/launch"
rsync -a --exclude __pycache__ --exclude '*.pyc' "$ROOT/vega_annotator" "$ROOT/vendor" "$ROOT/requirements" "$APP/Contents/Resources/app/"

ICONSET="$WORK/icon.iconset"
mkdir -p "$ICONSET"
python3 "$ROOT/packaging/make_icon.py" "$ICONSET/icon_512x512@2x.png" 1024
for pair in "16 icon_16x16" "32 icon_16x16@2x" "32 icon_32x32" "64 icon_32x32@2x" \
            "128 icon_128x128" "256 icon_128x128@2x" "256 icon_256x256" "512 icon_256x256@2x" "512 icon_512x512"; do
  set -- $pair
  sips -z "$1" "$1" "$ICONSET/icon_512x512@2x.png" --out "$ICONSET/$2.png" > /dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/icon.icns"

# The app always installs to Applications in the person's home folder, so no password is needed.
pkgbuild --analyze --root "$WORK/root" "$WORK/component.plist" > /dev/null
plutil -replace 0.BundleIsRelocatable -bool false "$WORK/component.plist"
chmod +x "$ROOT/packaging/mac/scripts/postinstall"
pkgbuild --quiet --root "$WORK/root" --component-plist "$WORK/component.plist" --identifier "$IDENTIFIER" \
  --version "$VERSION" --install-location "/Applications" --scripts "$ROOT/packaging/mac/scripts" "$WORK/component.pkg"

cat > "$WORK/distribution.xml" <<XML
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
  <title>$APP_NAME</title>
  <welcome file="welcome.html" mime-type="text/html"/>
  <conclusion file="conclusion.html" mime-type="text/html"/>
  <domains enable_anywhere="false" enable_currentUserHome="true" enable_localSystem="false"/>
  <options customize="never" require-scripts="false" hostArchitectures="arm64"/>
  <choices-outline><line choice="default"/></choices-outline>
  <choice id="default" title="$APP_NAME"><pkg-ref id="$IDENTIFIER"/></choice>
  <pkg-ref id="$IDENTIFIER" version="$VERSION" onConclusion="none">component.pkg</pkg-ref>
</installer-gui-script>
XML
productbuild --quiet --distribution "$WORK/distribution.xml" --package-path "$WORK" --resources "$ROOT/packaging/mac/resources" "$OUT"
echo "$OUT"
