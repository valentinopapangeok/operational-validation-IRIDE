#!/bin/sh
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python_bin=${S5_VALIDATION_PYTHON:-"$repo_dir/.venv/bin/python"}
exec "$python_bin" "$repo_dir/validate.py" "$@"
