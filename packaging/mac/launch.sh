#!/bin/bash
# Start VEGA Annotator and open it in the browser. macOS stops any process an app leaves
# behind when the app exits, so launchd runs the home screen as a service. A server older
# than the installed code is restarted.
APP_NAME="VEGA Annotator"
LABEL="org.vicar.vega-annotator.home"
PORT="5050"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$HERE/../Resources/app"
SUPPORT="$HOME/Library/Application Support/$APP_NAME"
PYTHON="$SUPPORT/runtime/bin/python"
LOG="$SUPPORT/logs/home.log"
DOMAIN="gui/$(id -u)"
SERVICE="$DOMAIN/$LABEL"
AGENT_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
URL="http://127.0.0.1:$PORT/"
START_WAIT_TENTHS=300
STOP_WAIT_TENTHS=50

# Show one alert. The message travels as an argument, so a quote in a path cannot break it.
alert() {
  osascript -e 'on run argv' -e 'display alert (item 1 of argv) message (item 2 of argv)' -e 'end run' "$APP_NAME did not start" "$1" > /dev/null
}

# True when the server answers its health check.
healthy() { curl -s -m 2 "${URL}api/health" | grep -q '"ok":true'; }

# The process listening on the port, or nothing.
server_pid() { lsof -t -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -1; }

# The version of the running server, and the version of this app.
server_version() { curl -s -m 2 "${URL}api/health" | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p'; }
app_version() { /usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$HERE/../Info.plist" 2>/dev/null; }

# True when the runtime finished setup for this version's requirements.
runtime_ready() {
  [ -x "$PYTHON" ] && cat "$PROJECT/requirements/base.txt" "$PROJECT/requirements/mac.txt" | cmp -s - "$SUPPORT/runtime/.complete"
}

if [ "$(uname -m)" != "arm64" ]; then
  alert "This Mac has an Intel processor. PyTorch 2.11, which SAM3 needs, runs only on Macs with Apple chips."
  exit 1
fi

if ! runtime_ready; then
  osascript -e 'display dialog "VEGA Annotator needs Python and PyTorch. It will download them, up to about 1 GB, into its own folder and tell you each step. This takes a few minutes." with title "Set up VEGA Annotator" buttons {"Cancel", "Download"} default button "Download"' > /dev/null || exit 0
  # Setup replaces the Python a running server uses, so the server stops first.
  launchctl bootout "$SERVICE" > /dev/null 2>&1
  if ! /bin/bash "$HERE/../Resources/setup.sh"; then
    alert "Setup could not finish. Check your internet connection and open VEGA Annotator again. The reason is at the end of $HOME/Library/Logs/$APP_NAME install.log."
    exit 1
  fi
fi
mkdir -p "$SUPPORT/logs" "$(dirname "$AGENT_PLIST")"

# Write the service definition. It starts only when asked and stays stopped when stopped.
"$PYTHON" - "$AGENT_PLIST" "$LABEL" "$PROJECT" "$PYTHON" "$LOG" "$PORT" <<'PY'
import os
import plistlib
import sys

path, label, project, python, log, port = sys.argv[1:]
project = os.path.realpath(project)
service = {
    "Label": label,
    "ProgramArguments": [python, "-m", "vega_annotator.home", "--port", port],
    "WorkingDirectory": project,
    "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "PYTHONUTF8": "1"},
    "StandardOutPath": log,
    "StandardErrorPath": log,
    "ProcessType": "Interactive",
}
with open(path, "wb") as handle:
    plistlib.dump(service, handle)
PY

# A server from another version stops, so the new code runs.
pid="$(server_pid)"
if [ -n "$pid" ] && healthy && [ "$(server_version)" != "$(app_version)" ]; then
  kill "$pid"
  for _ in $(seq 1 "$STOP_WAIT_TENTHS"); do [ -z "$(server_pid)" ] && break; sleep 0.1; done
fi
if ! healthy; then
  # launchd keeps a loaded definition in memory, so reload it to read the file written above.
  launchctl bootout "$SERVICE" >> "$LOG" 2>&1
  launchctl bootstrap "$DOMAIN" "$AGENT_PLIST" >> "$LOG" 2>&1
  launchctl kickstart "$SERVICE" >> "$LOG" 2>&1
  for _ in $(seq 1 "$START_WAIT_TENTHS"); do healthy && break; sleep 0.1; done
  if ! healthy; then
    alert "The reason is at the end of $LOG."
    exit 1
  fi
fi
open -a "Google Chrome" "$URL" 2>/dev/null || open "$URL"
