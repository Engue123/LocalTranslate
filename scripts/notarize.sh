#!/bin/bash
# notarize script for LocalTranslate
set -e

APP="dist/LocalTranslate.app"
ZIP="dist/LocalTranslate.zip"
BUNDLE_ID="com.antigravity.localtranslate"

ditto -c -k --keepParent "$APP" "$ZIP"

echo "Submitting for notarization..."
xcrun notarytool submit "$ZIP" \
  --keychain-profile "AC_PASSWORD" \
  --wait

echo "Stapling ticket..."
xcrun stapler staple "$APP"

echo "Notarization complete."
