#!/usr/bin/env bash
set -euo pipefail

if command -v tectonic >/dev/null 2>&1; then
  echo "Tectonic already installed ✔"
  exit 0
fi

case "$(uname -s)" in
  Darwin)
    if command -v brew >/dev/null 2>&1; then
      echo "Installing Tectonic via Homebrew…"
      brew install tectonic
    else
      echo "Homebrew not found. Install Homebrew first or install MacTeX/TeX Live."
      exit 1
    fi
    ;;
  Linux)
    if command -v apt-get >/dev/null 2>&1; then
      echo "Installing Tectonic via apt…"
      sudo apt-get update && sudo apt-get install -y tectonic
    else
      echo "Please install Tectonic via your distro’s package manager."
      echo "Examples: 'dnf install tectonic' or 'pacman -S tectonic'."
      exit 1
    fi
    ;;
  MINGW*|MSYS*|CYGWIN*|Windows_NT)
    echo "On Windows, install via Chocolatey (Admin): choco install tectonic"
    echo "or via Scoop: scoop install tectonic"
    exit 1
    ;;
  *)
    echo "Unsupported OS. Please install Tectonic manually."
    exit 1
    ;;
esac

echo "Done."
