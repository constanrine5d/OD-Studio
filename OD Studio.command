#!/bin/bash
# Double-click on macOS (or run on Linux) to start OD Studio.
# Needs only Python 3.9 or newer; everything else is in this folder.
cd "$(dirname "$0")" || exit 1
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3.9 or newer is needed. Install it from https://www.python.org/downloads/ and try again."
  read -r -p "Press Enter to close."
  exit 1
fi
export PYTHONPATH="$PWD/src:$PWD/vendor${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" -m odstudio gui "$@"
