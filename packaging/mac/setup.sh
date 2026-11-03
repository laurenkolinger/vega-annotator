#!/bin/bash
# Download what VEGA Annotator runs on into the app's own folder: Python and PyTorch with
# the libraries VEGA's annotator uses. Nothing else on the computer changes, and no
# administrator password is needed. The app runs this on its first open, because macOS
# gives package installers no network. A failure stops here and leaves the reason in the log.
set -euo pipefail

APP_NAME="VEGA Annotator"
UV_VERSION="0.13.6"
PYTHON_VERSION="3.12"
HERE="$(cd "$(dirname "$0")" && pwd)"
SUPPORT="$HOME/Library/Application Support/$APP_NAME"
RUNTIME="$SUPPORT/runtime"
LOG="$HOME/Library/Logs/$APP_NAME install.log"

# Tell the person which step is running.
step() {
  echo "$1"
  osascript -e 'on run argv' -e 'display notification (item 1 of argv) with title "Setting up VEGA Annotator"' -e 'end run' "$1" > /dev/null 2>&1 || true
}

mkdir -p "$SUPPORT" "$(dirname "$LOG")"
exec > "$LOG" 2>&1
echo "Installing $APP_NAME on $(date)"

case "$(uname -m)" in
  arm64) UV_ARCH="aarch64" ;;
  *) echo "This Mac has an Intel processor. PyTorch 2.11, which SAM3 needs, runs only on Macs with Apple chips."; exit 2 ;;
esac

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
step "Step 1 of 3: downloading the setup tool"
curl -fsSL --retry 3 --connect-timeout 20 --max-time 300 "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$UV_ARCH-apple-darwin.tar.gz" \
  | tar -xz -C "$WORK" --strip-components=1
export UV_PYTHON_INSTALL_DIR="$SUPPORT/python"
export UV_CACHE_DIR="$WORK/cache"

step "Step 2 of 3: downloading Python"
rm -rf "$RUNTIME"
"$WORK/uv" venv --python "$PYTHON_VERSION" --python-preference only-managed "$RUNTIME"

step "Step 3 of 3: downloading PyTorch and the annotator's libraries"
"$WORK/uv" pip install --python "$RUNTIME/bin/python" -r "$HERE/app/requirements/mac.txt"

echo "Checking the result"
"$RUNTIME/bin/python" -c 'import torch, transformers, cv2, flask; from transformers import Sam3TrackerModel; print("torch", torch.__version__, "transformers", transformers.__version__, "mps", torch.backends.mps.is_available())'

# The app treats a runtime without this copy of the requirements as unfinished and sets up again.
cat "$HERE/app/requirements/base.txt" "$HERE/app/requirements/mac.txt" > "$RUNTIME/.complete"
echo "Installed."
