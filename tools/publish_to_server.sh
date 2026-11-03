#!/bin/bash
# Put the installers, the instructions, and SAM3 in a server folder, through the mainframe,
# which syncs that folder with the server. Set VEGA_PUBLISH_DEST to it. Run it from this
# repository on a Mac with `ssh vicar-mainframe` set up, after building both installers.
# Projects go separately, with tools/publish_project.py. Re-running it is safe: SAM3 copies
# only when a file's size differs, and every file is checked against its source.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${VEGA_PUBLISH_HOST:-vicar-mainframe}"
DEST="${VEGA_PUBLISH_DEST:?Set VEGA_PUBLISH_DEST to the VEGA_Annotator folder on the mainframe.}"
VERSION="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from vega_annotator import VERSION; print(VERSION)' "$ROOT")"
PKG="$ROOT/dist/VEGAAnnotator-$VERSION.pkg"
ZIP="$ROOT/dist/VEGAAnnotator-$VERSION-Windows.zip"

for file in "$PKG" "$ZIP"; do
  if [ ! -f "$file" ]; then
    echo "publish_to_server: $file is missing. Build the installers first." >&2
    exit 1
  fi
done

ssh -o BatchMode=yes "$HOST" "mkdir -p '$DEST/install' '$DEST/sam3' '$DEST/projects'"
rsync -t "$PKG" "$ZIP" "$HOST:$DEST/install/"
# Through a pipe, because rsync splits a remote path at its space.
ssh -o BatchMode=yes "$HOST" "cat > '$DEST/START HERE.txt'" < "$ROOT/docs/START_HERE.txt"
rsync -t "$ROOT/vendor/SAM_LICENSE.txt" "$HOST:$DEST/sam3/SAM_LICENSE.txt"
ssh -o BatchMode=yes "$HOST" python3 - --dest "$DEST/sam3" < "$ROOT/tools/publish_sam3.py"
echo "The installers, the instructions, and SAM3 are in $DEST on $HOST."
