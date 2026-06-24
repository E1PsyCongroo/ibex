#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"

find "$ROOT" -mindepth 1 -maxdepth 1 -type d | sort -V | while read -r dir; do
  name="$(basename "$dir")"

  if [[ ! "$name" =~ ^[0-9]+$ ]]; then
    echo "skip: $dir is not numeric"
    continue
  fi

  target="$dir/$name"

  if [ -d "$target" ]; then
    echo "delete: $target"
    rm -rf -- "$target"
  else
    echo "skip: $target not found"
  fi
done
