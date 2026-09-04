#!/usr/bin/env bash
set -euo pipefail

action=""
distribution=""
every="1"
run_now="false"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --install|--uninstall|--status) action="${1#--}"; shift ;;
    --distribution) distribution="$2"; shift 2 ;;
    --every) every="$2"; shift 2 ;;
    --run-now) run_now="true"; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$action" || -z "$distribution" ]]; then
  echo "Action and --distribution are required." >&2
  exit 2
fi
if [[ ! "$every" =~ ^[1-9][0-9]*$ ]]; then
  echo "--every must be a positive integer." >&2
  exit 2
fi
distribution="$(cd "$distribution" && pwd -P)"
launcher="$distribution/scripts/hoh.sh"
if [[ ! -f "$launcher" ]]; then
  echo "Portable launcher not found: $launcher" >&2
  exit 1
fi

xml_escape() {
  local value="$1"
  value="${value//&/&amp;}"
  value="${value//</&lt;}"
  value="${value//>/&gt;}"
  printf '%s' "$value"
}

case "$(uname -s)" in
  Linux)
    service_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    service="$service_dir/hoh-workspace.service"
    timer="$service_dir/hoh-workspace.timer"
    case "$action" in
      install)
        mkdir -p "$service_dir"
        if [[ "$launcher" == *$'\n'* || "$launcher" == *'"'* ]]; then
          echo "Distribution path contains unsupported characters." >&2; exit 1
        fi
        printf '%s\n' \
          '[Unit]' 'Description=HoH workspace scheduler' '' \
          '[Service]' 'Type=oneshot' "WorkingDirectory=$distribution" \
          "ExecStart=\"$launcher\" workspace run-due --json" > "$service"
        printf '%s\n' \
          '[Unit]' 'Description=Run due HoH project queues' '' \
          '[Timer]' "OnBootSec=${every}min" "OnUnitActiveSec=${every}min" 'Persistent=true' '' \
          '[Install]' 'WantedBy=timers.target' > "$timer"
        systemctl --user daemon-reload
        systemctl --user enable --now hoh-workspace.timer
        if [[ "$run_now" == "true" ]]; then
          systemctl --user start hoh-workspace.service
        fi
        ;;
      uninstall)
        systemctl --user disable --now hoh-workspace.timer 2>/dev/null || true
        rm -f "$service" "$timer"
        systemctl --user daemon-reload
        ;;
      status)
        if systemctl --user is-enabled hoh-workspace.timer >/dev/null 2>&1; then
          echo "installed=true"
          systemctl --user --no-pager status hoh-workspace.timer || true
        else
          echo "installed=false"
        fi
        ;;
    esac
    ;;
  Darwin)
    label="com.hoh.workspace-scheduler"
    agents="$HOME/Library/LaunchAgents"
    plist="$agents/$label.plist"
    domain="gui/$(id -u)"
    case "$action" in
      install)
        mkdir -p "$agents"
        escaped_launcher="$(xml_escape "$launcher")"
        escaped_distribution="$(xml_escape "$distribution")"
        seconds=$((every * 60))
        printf '%s\n' \
          '<?xml version="1.0" encoding="UTF-8"?>' \
          '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">' \
          '<plist version="1.0"><dict>' \
          '<key>Label</key><string>com.hoh.workspace-scheduler</string>' \
          '<key>ProgramArguments</key><array>' \
          "<string>$escaped_launcher</string><string>workspace</string><string>run-due</string><string>--json</string>" \
          '</array>' \
          "<key>WorkingDirectory</key><string>$escaped_distribution</string>" \
          "<key>StartInterval</key><integer>$seconds</integer>" \
          '<key>RunAtLoad</key><true/>' \
          '</dict></plist>' > "$plist"
        launchctl bootout "$domain/$label" 2>/dev/null || true
        launchctl bootstrap "$domain" "$plist"
        if [[ "$run_now" == "true" ]]; then
          launchctl kickstart "$domain/$label"
        fi
        ;;
      uninstall)
        launchctl bootout "$domain/$label" 2>/dev/null || true
        rm -f "$plist"
        ;;
      status)
        if launchctl print "$domain/$label" >/dev/null 2>&1; then
          echo "installed=true"
          launchctl print "$domain/$label"
        else
          echo "installed=false"
        fi
        ;;
    esac
    ;;
  *) echo "Unsupported scheduler platform: $(uname -s)" >&2; exit 1 ;;
esac
