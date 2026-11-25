# Bundled fonts

Both fonts use the SIL Open Font License 1.1 (licence texts next to them). Neither licence lists a
Reserved Font Name, so subsetting is allowed and the names stay the same.

| file | source | version |
|---|---|---|
| `Inter-{Regular,Medium,SemiBold}.woff2` | `web/` in `https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip` (sha256 `9883fdd4a49d4fb66bd8177ba6625ef9a64aa45899767dde3d36aa425756b11e`) | 4.001 |
| `JetBrainsMono-{Regular,Medium,SemiBold}.woff2` | `fonts/webfonts/` in `https://github.com/JetBrains/JetBrainsMono/releases/download/v2.304/JetBrainsMono-2.304.zip` (sha256 `6f6376c6ed2960ea8a963cd7387ec9d76e3f629125bc33d1fdcd7eb7012f7bbf`) | 2.304 |

Subset with fonttools `pyftsubset` to Latin-1 plus common punctuation and arrows
(`--unicodes="U+0020-007E,U+00A0-00FF,U+0131,U+0152-0153,U+02C6,U+02DA,U+02DC,U+2013-2014,U+2018-201A,U+201C-201E,U+2022,U+2026,U+2032-2033,U+2039-203A,U+20AC,U+2122,U+2190-2193,U+2212,U+2264-2265"`
`--layout-features=kern,liga,calt,tnum,case --flavor=woff2`). About 140 KB for all six files.
Characters outside this set (other scripts in code snippets) fall back to the system font.
