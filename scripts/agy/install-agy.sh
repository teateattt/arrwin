#!/usr/bin/env bash
set -euo pipefail

AGY_VERSION="1.5.18"
AGY_ARCHIVE_URL="https://storage.googleapis.com/antigravity-public/antigravity-cli/1.5.18-6386224290725888/linux-x64/cli_linux_x64.tar.gz"
AGY_ARCHIVE_SHA512="ef379ec869719112f696e8c7cfddf776618712e58fc38e53df80801d6528abb4677782c7c8778f9c09c9123fa44c2c32675d228c06f2a8f7a8f4214e75f5bb26"

STAGING_DIR="${RUNNER_TEMP:-/tmp}/agy-cli-$AGY_VERSION"
ARCHIVE_PATH="$STAGING_DIR/agy.tar.gz"
EXTRACT_DIR="$STAGING_DIR/extracted"
INSTALL_DIR="$HOME/.local/bin"

cleanup() {
  rm -rf "$STAGING_DIR"
}
trap cleanup EXIT

mkdir -p "$STAGING_DIR" "$EXTRACT_DIR" "$INSTALL_DIR"

curl --fail --silent --show-error --location \
  --retry 3 --retry-delay 2 \
  "$AGY_ARCHIVE_URL" \
  --output "$ARCHIVE_PATH"

printf '%s  %s\n' "$AGY_ARCHIVE_SHA512" "$ARCHIVE_PATH" | sha512sum -c -
tar -xzf "$ARCHIVE_PATH" -C "$EXTRACT_DIR" antigravity
install -m 0755 "$EXTRACT_DIR/antigravity" "$INSTALL_DIR/agy"

echo "$INSTALL_DIR" >> "$GITHUB_PATH"
VERSION_OUTPUT="$("$INSTALL_DIR/agy" --version)"
echo "$VERSION_OUTPUT"

if [[ "$VERSION_OUTPUT" != *"$AGY_VERSION"* ]]; then
  echo "Installed Agy version does not match $AGY_VERSION" >&2
  exit 1
fi
