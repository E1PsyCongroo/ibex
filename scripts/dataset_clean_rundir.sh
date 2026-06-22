#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"

for i in {0..4}; do
  target="$ROOT/$i/$i"

  if [ -d "$target" ]; then
    echo "delete: $target"
    rm -rf -- "$target"
  else
    echo "skip: $target not found"
  fi
done
