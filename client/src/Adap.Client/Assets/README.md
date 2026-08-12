# Client UI assets

Drop image files here. Anything in this folder is compiled into the client as
an embedded resource, so the published single-file exe keeps working with no
loose files to ship alongside it.

Subfolders are fine and are preserved in the resource name, e.g.
`Assets/Icons/connect.png` becomes `Adap.Client.Assets.Icons.connect.png`.

## What works well

- **PNG** with alpha for anything layered over a background.
- **ICO** for the window/taskbar icon; include 16, 32, 48 and 256 px frames in
  the one file.
- Provide bitmaps at the largest size you want them drawn, or at 2x. The client
  runs per-monitor DPI aware, so undersized art is visibly soft on a 150%
  display.
- **SVG is not usable directly** - WinForms has no vector support and the
  client is dependency-free by design. Export to PNG at 1x and 2x instead.

## Naming

Lowercase, hyphen-separated, describing the role rather than the appearance:
`connect-button.png`, not `green-arrow.png`. Roles survive a restyle.
