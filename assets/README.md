# Assets

Place `icon.icns` (macOS) or `icon.ico` (Windows) here.
Then update `main.spec` to point to it:
```python
icon='assets/icon.icns',
```

To generate `.icns` from `.png` on macOS:
```bash
mkdir icon.iconset
sips -z 512 512 icon.png --out icon.iconset/icon_512x512.png
iconutil -c icns icon.iconset -o icon.icns
```
