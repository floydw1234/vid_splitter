#!/usr/bin/env bash
  set -euo pipefail

  PROJECT_DIR="${HOME}/Documents/codingProj/vid_splitter"
  CONTAINER_NAME="jellyfin"
  ZIP_NAME="smart-branching-0.1.0.0.zip"
  PLUGIN_NAME="SmartBranching"

  cd "$PROJECT_DIR"
  cd csharp_plugin

  dotnet build SmartBranching.Plugin.sln -c Release
  python3 scripts/package_plugin.py --build-output bin/Release/net8.0 --output-dir dist

  ZIP_PATH="$PROJECT_DIR/csharp_plugin/dist/$ZIP_NAME"
  TMP_DIR="/tmp/smart-branching"

  mkdir -p "$TMP_DIR"
  rm -rf "$TMP_DIR"/*
  python3 -m zipfile -e "$ZIP_PATH" "$TMP_DIR"

  docker exec "$CONTAINER_NAME" mkdir -p /config/plugins/"$PLUGIN_NAME"
  docker cp "$TMP_DIR"/. "$CONTAINER_NAME":/config/plugins/"$PLUGIN_NAME"/
  docker restart "$CONTAINER_NAME"

  echo "Installed to /config/plugins/$PLUGIN_NAME"
  docker exec "$CONTAINER_NAME" ls -l /config/plugins/"$PLUGIN_NAME"
  docker logs "$CONTAINER_NAME" --tail 200 | grep -i SmartBranching || true
