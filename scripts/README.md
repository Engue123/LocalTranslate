# Packaging Scripts

## Prerequisites
- Apple Developer ID Application certificate
- App-specific password saved in Keychain as "AC_PASSWORD"

## Steps
1. Build: `pyinstaller main.spec`
2. Sign: `./scripts/sign.sh`
3. Notarize: `./scripts/notarize.sh`
4. Distribute `dist/LocalTranslate.app` or create a DMG.
