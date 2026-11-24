Original plugin artwork. Render and embed it in the version-1 manifest:

```sh
rsvg-convert -w 128 -h 128 assets/icon.svg -o assets/icon.png
sloppy-plugin-setup set-icon . --icon assets/icon.png
```

Commit the artwork and manifest together. The catalogue carries the embedded PNG.
