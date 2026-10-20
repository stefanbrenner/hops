#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    echo "Installing dependencies..."
    "$VENV_DIR/bin/pip" install setuptools
    "$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"
    "$VENV_DIR/bin/pip" install "$SCRIPT_DIR"

fi

exec "$VENV_DIR/bin/python" -m hops
