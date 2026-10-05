#!/usr/bin/env bash
set -euo pipefail
case "${1:---desktop}" in
 --desktop) repo=DriveBridge-Desktop ;;
 --web) repo=DriveBridge-Web ;;
 *) echo 'Uso: bash install.sh --desktop | --web' >&2; exit 2 ;;
esac
task_installer=$(mktemp)
trap 'rm -f "$task_installer"' EXIT
curl -fsSL "https://raw.githubusercontent.com/poajefersonld-1983/$repo/main/install.sh" -o "$task_installer"
bash "$task_installer"
