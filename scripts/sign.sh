#!/bin/bash
# codesign script for LocalTranslate
set -e

APP="dist/LocalTranslate.app"
IDENTITY="Developer ID Application: YOUR_NAME (TEAM_ID)"

echo "Signing $APP..."
codesign --deep --force --verify --verbose \
  --sign "$IDENTITY" \
  --options runtime \
  "$APP"

echo "Verifying signature..."
codesign --verify --deep --strict "$APP"
codesign --display --verbose=4 "$APP"

echo "Done. Next: run notarize.sh"
