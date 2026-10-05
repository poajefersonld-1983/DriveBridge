#!/bin/sh
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
cd "$project_dir" || exit 1
exec "$project_dir/.venv/bin/python" -m drivebridge "$@"
