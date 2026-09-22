# tools/poppler

Portable Windows build of Poppler (provides `pdftoppm.exe`), used by
`dd1391_parser.py` to render PDF pages to images when text extraction is
corrupted (scrambled font encodings some DD1391s use).

Not committed (91MB, see `.gitignore`) — download it locally with:

```
python tools/fetch_poppler.py
```

Source: https://github.com/oschwartz10612/poppler-windows releases.
