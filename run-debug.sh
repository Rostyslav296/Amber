#!/usr/bin/env bash
# py2app launcher/debugger (works with macOS bash 3.2)
set -euo pipefail

bold(){ printf "\033[1m%s\033[0m\n" "$*"; }
info(){ printf "🔹 %s\n" "$*"; }
ok(){   printf "✅ %s\n" "$*"; }
warn(){ printf "⚠️  %s\n" "$*"; }
err(){  printf "🛑 %s\n" "$*" >&2; }
die(){  err "$1"; exit 1; }

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

DIST_DIR="${DIST_DIR:-$HERE/dist}"
BUILD_DIR="${BUILD_DIR:-$HERE/build}"
LOG_DIR="$HERE/logs"; mkdir -p "$LOG_DIR"

DO_REBUILD=false
DO_REVEAL=false
DO_QT_DEBUG=false
DO_DUMP=false
DO_CLEAN=false
DO_FALLBACK=false
DO_CLEAR_LOCK=false
DO_KILL_RUNNING=false

# --- parse flags ---
for a in "${@:-}"; do
  case "$a" in
    --rebuild)  DO_REBUILD=true ;;
    --reveal)   DO_REVEAL=true ;;
    --qt-debug) DO_QT_DEBUG=true ;;
    --dump)     DO_DUMP=true ;;
    --clean)    DO_CLEAN=true ;;
    --fallback) DO_FALLBACK=true ;;
    --clear-lock) DO_CLEAR_LOCK=true ;;
    --kill-running) DO_KILL_RUNNING=true ;;
    --help|-h)
      cat <<EOF
$(bold "run-debug.sh — diagnose py2app 'Launch error'")
Flags:
  --rebuild      Build first with ./build.sh
  --reveal       Reveal the .app in Finder and exit
  --qt-debug     Enable Qt plugin loader logs (QT_DEBUG_PLUGINS=1)
  --dump         Print otool -L, codesign, Info.plist summary
  --fallback     Run appearance.py via python (bypass .app)
  --clean        Remove build/ and dist/
  --clear-lock   Clear the installer lock file before running
  --kill-running Kill any running AgentF processes before launch
Env:
  APP_NAME       Bundle name without .app (e.g., AgentF)
  APP_PATH       Full path to an .app (overrides APP_NAME)
  DIST_DIR       Dist dir (default: ./dist)
EOF
      exit 0 ;;
    *) ;;
  esac
done

$DO_CLEAN && { info "Cleaning build/ dist/…"; rm -rf "$BUILD_DIR" "$DIST_DIR"; ok "Cleaned."; exit 0; }
$DO_REBUILD && { [[ -x "$HERE/build.sh" ]] || die "build.sh not found"; info "Building…"; "$HERE/build.sh"; }

# --- fallback: run python directly ---
if $DO_FALLBACK; then
  bold "Running fallback (python appearance.py)…"
  LOG_FILE="$LOG_DIR/fallback_$(date +%F_%H-%M-%S).log"
  set +e
  /usr/bin/env python3 "$HERE/appearance.py" 2>&1 | tee -a "$LOG_FILE"
  code=${PIPESTATUS[0]}; set -e
  exit $code
fi

# --- helpers ---
list_dist() {
  info "Looking in: $DIST_DIR"
  if [[ -d "$DIST_DIR" ]]; then
    ls -lah "$DIST_DIR" || true
    printf "\n"; info "Found .app candidates:"
    /usr/bin/find "$DIST_DIR" -maxdepth 1 -type d -name "*.app" -print 2>/dev/null \
      | sed 's/^/  - /' || true
    printf "\n"
  else
    warn "dist directory does not exist: $DIST_DIR"
  fi
}

newest_app_in_dist() {
  # print newest *.app by mtime; robust with spaces
  # output: absolute path or empty
  local out
  out="$(/usr/bin/find "$DIST_DIR" -maxdepth 1 -type d -name "*.app" -print0 2>/dev/null \
        | xargs -0 -I{} /bin/sh -c 'printf "%s\t%s\n" "$(stat -f "%m" "{}" 2>/dev/null || echo 0)" "{}"' \
        | sort -rn | awk -F'\t' 'NR==1 {print $2}')"
  printf "%s" "$out"
}

find_app_bundle() {
  if [[ -n "${APP_PATH:-}" && -d "${APP_PATH}" ]]; then
    printf "%s" "$APP_PATH"; return
  fi
  if [[ -n "${APP_NAME:-}" && -d "$DIST_DIR/${APP_NAME}.app" ]]; then
    printf "%s" "$DIST_DIR/${APP_NAME}.app"; return
  fi
  newest_app_in_dist
}

find_bundle_binary() {
  local app="$1"
  local macos_dir="$app/Contents/MacOS"
  [[ -d "$macos_dir" ]] || die "Missing $macos_dir"
  local base; base="$(basename "$app" .app)"
  if [[ -x "$macos_dir/$base" ]]; then
    printf "%s" "$macos_dir/$base"; return
  fi
  /usr/bin/find "$macos_dir" -type f -perm +111 -print 2>/dev/null | head -n 1
}

clear_installer_lock() {
  local lock_file="$HOME/Library/Application Support/AgentF/.installer.lock"
  if [[ -f "$lock_file" ]]; then
    info "Clearing stale lock: $lock_file"
    rm -f "$lock_file"
    ok "Lock cleared."
  else
    info "No lock file found."
  fi
}

kill_running_processes() {
  local pids
  pids="$(pgrep -f "AgentF" | grep -v "$$" || true)"  # Exclude self
  if [[ -n "$pids" ]]; then
    warn "Killing running AgentF processes: $(echo "$pids" | tr '\n' ' ')"
    echo "$pids" | xargs kill -9 2>/dev/null || true
    ok "Processes killed."
  else
    info "No running AgentF processes found."
  fi
}

# --- clear lock if requested ---
$DO_CLEAR_LOCK && clear_installer_lock

# --- kill running if requested ---
$DO_KILL_RUNNING && kill_running_processes

# --- find the app ---
APP="$(find_app_bundle)"
if [[ -z "$APP" || ! -d "$APP" ]]; then
  list_dist
  die "No .app found in $DIST_DIR. (Set APP_NAME=YourApp or APP_PATH=/full/path/My.app)"
fi

$DO_REVEAL && { info "Revealing $(basename "$APP")"; open -R "$APP"; exit 0; }

BIN="$(find_bundle_binary "$APP")"; [[ -n "$BIN" && -x "$BIN" ]] || { list_dist; die "No executable under $(basename "$APP")/Contents/MacOS"; }
MACOS_DIR="$(dirname "$BIN")"
RES="$APP/Contents/Resources"
APP_BASE="$(basename "$APP" .app)"
STAMP="$(date +%F_%H-%M-%S)"
LOG_FILE="$LOG_DIR/${APP_BASE}_$STAMP.log"

# --- clear quarantine (frequent cause of "Launch error") ---
if /usr/bin/xattr -p com.apple.quarantine "$APP" >/dev/null 2>&1; then
  warn "Clearing quarantine attributes…"
  /usr/bin/xattr -dr com.apple.quarantine "$APP" || true
fi

bold "Launching $APP_BASE (debug)"
info "Bundle:   $APP"
info "Binary:   $BIN"
info "Resources:$RES"
info "Log file: $LOG_FILE"
info "macOS:    $(sw_vers -productVersion) $(uname -m)"
echo

# optional diagnostics
if $DO_DUMP; then
  printf "\n--- Info.plist ---\n"
  /usr/libexec/PlistBuddy -c "Print" "$APP/Contents/Info.plist" | sed 's/^/PLIST: /' || true
  printf "\n--- codesign ---\n"
  codesign -dv --verbose=4 "$APP" 2>&1 | sed 's/^/CS: /' || true
  printf "\n--- otool -L (binary) ---\n"
  otool -L "$BIN" | sed 's/^/OTL: /' || true
  printf "\n--- Qt plugins present ---\n"
  /usr/bin/find "$RES/plugins" -maxdepth 2 -type f 2>/dev/null | sed 's/^/PLUG: /' || true
  echo
fi

# env
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
export APP_RESOURCES_DIR="$RES"
$DO_QT_DEBUG && export QT_DEBUG_PLUGINS=1

ok "Running… (Ctrl+C to stop)"
set +e
(
  cd "$MACOS_DIR"
  "$BIN"
) 2>&1 | tee -a "$LOG_FILE"
EXIT_CODE=${PIPESTATUS[0]}
set -e
echo

if [[ $EXIT_CODE -eq 0 ]]; then
  ok "$APP_BASE exited cleanly."
  exit 0
fi

warn "$APP_BASE exited with code $EXIT_CODE"
info "Checking for recent crashes (last 15m)…"
if command -v log >/dev/null 2>&1; then
  log show --last 15m --predicate "eventType == crash" --style syslog \
    | grep -Ei "$APP_BASE|Contents/MacOS" \
    | tail -n 300 | sed 's/^/CRASH: /' | tee -a "$LOG_FILE" || true
fi

err "See log: $LOG_FILE"
exit "$EXIT_CODE"