#!/bin/bash
# Download what VEGA Annotator runs on into the app's own folder: Python and PyTorch with
# the libraries VEGA's annotator uses, then SAM3 from the lab's GitHub release. Nothing else
# on the computer changes, and no administrator password is needed. The app runs this on its
# first open, because macOS gives package installers no network. A failure in Steps 1 to 3
# stops here and leaves the reason in the log. Step 4 runs the app's own SAM3 downloader with
# the Python that Steps 2 and 3 installed. When it fails, Python stays installed, the app
# still opens, and DOWNLOAD SAM3 on its home screen continues the same download.
# Usage: setup.sh [--sam3-only]. --sam3-only runs Step 4 alone, for a Mac whose Python is ready.
set -euo pipefail

APP_NAME="VEGA Annotator"
UV_VERSION="0.13.6"
PYTHON_VERSION="3.12"
USAGE_EXIT=64
HERE="$(cd "$(dirname "$0")" && pwd)"
SUPPORT="$HOME/Library/Application Support/$APP_NAME"
RUNTIME="$SUPPORT/runtime"
LOG="$HOME/Library/Logs/$APP_NAME install.log"

case "${1:-}" in
  "") SAM3_ONLY=0 ;;
  --sam3-only) SAM3_ONLY=1 ;;
  *) echo "Usage: setup.sh [--sam3-only]" >&2; exit "$USAGE_EXIT" ;;
esac

# Tell the person which step is running.
step() {
  echo "$1"
  osascript -e 'on run argv' -e 'display notification (item 1 of argv) with title "Setting up VEGA Annotator"' -e 'end run' "$1" > /dev/null 2>&1 || true
}

# Download SAM3 with the app's own downloader. Its failure never fails setup: the downloader
# saves the reason, and the app then opens without SAM3 and waits for the person to click.
install_sam3() {
  step "$1"
  if ! (cd "$HERE/app" && "$RUNTIME/bin/python" -m vega_annotator.sam3_download --setup); then
    step "SAM3 did not download. Click DOWNLOAD SAM3 on the home screen to try again."
  fi
}

mkdir -p "$SUPPORT" "$(dirname "$LOG")"
# A full setup starts the log again. --sam3-only adds to it, so the reason the first setup's
# SAM3 download stopped stays above this run's lines.
if [ "$SAM3_ONLY" = 1 ]; then
  exec >> "$LOG" 2>&1
else
  exec > "$LOG" 2>&1
fi
echo "Installing $APP_NAME on $(date)"

case "$(uname -m)" in
  arm64) UV_ARCH="aarch64" ;;
  *) echo "This Mac has an Intel processor. PyTorch 2.11, which SAM3 needs, runs only on Macs with Apple chips."; exit 2 ;;
esac

if [ "$SAM3_ONLY" = 1 ]; then
  install_sam3 "Downloading SAM3 (3.4 GB)"
  exit 0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
step "Step 1 of 4: downloading the setup tool"
curl -fsSL --retry 3 --connect-timeout 20 --max-time 300 "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$UV_ARCH-apple-darwin.tar.gz" \
  | tar -xz -C "$WORK" --strip-components=1
export UV_PYTHON_INSTALL_DIR="$SUPPORT/python"
export UV_CACHE_DIR="$WORK/cache"

step "Step 2 of 4: downloading Python"
rm -rf "$RUNTIME"
"$WORK/uv" venv --python "$PYTHON_VERSION" --python-preference only-managed "$RUNTIME"

step "Step 3 of 4: downloading PyTorch and the annotator's libraries"
"$WORK/uv" pip install --python "$RUNTIME/bin/python" -r "$HERE/app/requirements/mac.txt"

echo "Checking the result"
"$RUNTIME/bin/python" -c 'import torch, transformers, cv2, flask; from transformers import Sam3TrackerModel; print("torch", torch.__version__, "transformers", transformers.__version__, "mps", torch.backends.mps.is_available())'

# The app treats a runtime without this copy of the requirements as unfinished and sets up again.
# It is written before Step 4, so Python counts as installed whatever happens to SAM3.
cat "$HERE/app/requirements/base.txt" "$HERE/app/requirements/mac.txt" > "$RUNTIME/.complete"
install_sam3 "Step 4 of 4: downloading SAM3 (3.4 GB)"
echo "Installed."
