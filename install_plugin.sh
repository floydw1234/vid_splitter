#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="${JELLYFIN_PLUGIN_DIR:-$HOME/servers/jellyfin/config/plugins}"
PLUGIN_NAME="Jellyfin.Plugin.SmartBranching"
BUILD_DIR="${SCRIPT_DIR}/csharp_plugin/bin/Release/net8.0"

echo "=== Smart Branching Plugin Installer ==="
echo "Plugin dir: ${PLUGIN_DIR}"
echo "Build dir:  ${BUILD_DIR}"
echo ""

# Build
echo "Building plugin..."
cd "${SCRIPT_DIR}/csharp_plugin"
dotnet build SmartBranching.Plugin.sln -c Release
cd "${SCRIPT_DIR}"
echo ""

# Check build output
if [ ! -f "${BUILD_DIR}/${PLUGIN_NAME}.dll" ]; then
    echo "ERROR: Build output not found at ${BUILD_DIR}"
    exit 1
fi

# Check plugin dir exists
if [ ! -d "${PLUGIN_DIR}" ]; then
    echo "ERROR: Plugin directory does not exist: ${PLUGIN_DIR}"
    echo "Set JELLYFIN_PLUGIN_DIR to the correct path, e.g.:"
    echo "  JELLYFIN_PLUGIN_DIR=/usr/share/jellyfin/plugins ./install_plugin.sh"
    exit 1
fi

# Find old files
OLD_FILES=()
for f in "${PLUGIN_DIR}"/${PLUGIN_NAME}.*; do
    [ -e "$f" ] && OLD_FILES+=("$f")
done

if [ ${#OLD_FILES[@]} -gt 0 ]; then
    echo "Found existing plugin files:"
    for f in "${OLD_FILES[@]}"; do
        echo "  $f"
    done

    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    BACKUP_DIR="${PLUGIN_DIR}/.backup_${TIMESTAMP}"
    mkdir -p "${BACKUP_DIR}"

    echo ""
    echo "Backing up to: ${BACKUP_DIR}"
    for f in "${OLD_FILES[@]}"; do
        mv "$f" "${BACKUP_DIR}/"
    done
    echo "Removed old files."
else
    echo "No existing plugin files found."
fi

# Copy new files
echo ""
echo "Installing new plugin files..."
cp "${BUILD_DIR}/${PLUGIN_NAME}.dll" "${PLUGIN_DIR}/"
cp "${BUILD_DIR}/${PLUGIN_NAME}.deps.json" "${PLUGIN_DIR}/"

# Copy ZstdSharp if present
if [ -f "${BUILD_DIR}/ZstdSharp.dll" ]; then
    cp "${BUILD_DIR}/ZstdSharp.dll" "${PLUGIN_DIR}/"
fi

echo ""
echo "Installed:"
ls -la "${PLUGIN_DIR}"/${PLUGIN_NAME}.*
echo ""
echo "=== Done ==="
echo "Restart Jellyfin to apply changes."
