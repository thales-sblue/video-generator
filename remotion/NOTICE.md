# Third-party notices — `remotion/`

## Remotion

This directory uses **Remotion** (https://www.remotion.dev), which is **not**
released under a permissive open-source licence. It is source-available under the
Remotion Licence:

- Free for individuals, non-profit / not-for-profit organisations, and for-profit
  organisations with **up to 3 people** operating the Remotion software, and for
  evaluation.
- A paid **Company Licence** is mandatory once four or more people across all
  involved parties operate it. See https://www.remotion.pro/license and
  https://www.remotion.dev/docs/license.

**Gate decision (2026-09-05):** integrated under the free-tier small-team
exemption — this project is operated by ≤3 people. If that changes, a Company
Licence must be acquired before further commercial use. This is the one
dependency in the repository that is not permissively licensed; it is confined to
this directory and invoked only through `video_generator.adapters.remotion`,
which fails closed when the toolchain is absent.

## Inter (font)

`public/fonts/Inter-Variable.ttf` — Inter by Rasmus Andersson, licensed under the
**SIL Open Font License 1.1**. Full licence text in `public/fonts/OFL.txt`.
Commercial use permitted; the font itself may not be sold on its own.

## Node.js

Runtime is a pinned, checksum-verified portable Node (`config/node-lock.json`),
unpacked under `.local-tools/node/` and never added to the global PATH — same
policy as the vendored FFmpeg.
