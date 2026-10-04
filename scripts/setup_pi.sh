#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
project_dir="$(pwd -P)"

if [[ "$EUID" -eq 0 ]]; then
  echo "Run this script as the user that should run the scraper, not with sudo." >&2
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "This setup requires a Linux host with systemd." >&2
  exit 1
fi

# Only unit installation needs elevated privileges; keep the venv user-owned.
sudo -v

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt

# Escape paths used in quoted ExecStart and EnvironmentFile values.
# ExecStart also interprets dollar signs, unlike EnvironmentFile.
escape_systemd_path() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//%/%%}"
  printf '%s' "$value"
}

if [[ "$project_dir" == *$'\n'* || "$project_dir" == *$'\r'* ]]; then
  echo "The project path cannot contain newline characters." >&2
  exit 1
fi

# WorkingDirectory takes a literal path (including spaces), not a quoted word.
# Only systemd specifiers need escaping here; quotes/backslashes remain literal.
working_dir="${project_dir//%/%%}"
escaped_project_dir="$(escape_systemd_path "$project_dir")"
exec_project_dir="${escaped_project_dir//\$/\$\$}"
service_user="$(id -un)"
service_group="$(id -gn)"

service_content="$(< deploy/systemd/huur-scraper.service)"
service_content="${service_content//@WORKING_DIR@/"$working_dir"}"
service_content="${service_content//@PROJECT_DIR@/"$escaped_project_dir"}"
service_content="${service_content//@EXEC_PROJECT_DIR@/"$exec_project_dir"}"
service_content="${service_content//@SERVICE_USER@/"$service_user"}"
service_content="${service_content//@SERVICE_GROUP@/"$service_group"}"

service_file="$(mktemp)"
trap 'rm -f "$service_file"' EXIT
printf '%s\n' "$service_content" > "$service_file"

sudo install -m 0644 "$service_file" /etc/systemd/system/huur-scraper.service
sudo install -m 0644 deploy/systemd/huur-scraper.timer /etc/systemd/system/huur-scraper.timer
sudo systemctl daemon-reload

echo "Pi environment ready (.venv); systemd units installed for $service_user in $project_dir."
echo "After configuring .env and testing, enable the timer with:"
echo "  sudo systemctl enable --now huur-scraper.timer"

