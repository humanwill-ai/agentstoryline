# Licensing and third-party materials

Original AgentStoryline source code, documentation and newly authored synthetic fixtures are licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).

Copyright 2026 HumanWill contributors.

The original code grant does not relicense imported recordings, retained workspace files, model outputs, user-supplied attachments, audio, trademarks, or independently licensed third-party material. Those remain subject to their respective rights and terms.

## Implementation and dependencies

The Python viewer uses the standard library and internal modules. Browser rendering uses local JavaScript, CSS, inline SVG and the project mascot PNGs. No third-party JavaScript bundle, font or notification sound is included in the reviewed source package. Participant robot avatars are project-local SVG implementations; provider badges use text initials rather than embedded third-party logo artwork.

The Sleepy Replicant mascot in `web/assets/sleepy-replicant-*.png` was generated using OpenAI's image generator and selected by the project owner. The master is retained unchanged; the smaller images are resampled exports. See [branding provenance](docs/BRANDING.md). This records the generation provenance and does not claim trademark clearance.

Python, the browser and the optional system `zstd` decoder are separately installed software; this repository does not bundle their runtimes. A future binary distribution must inventory its bundled components and preserve their notices.

## Artex

Artex is an independently licensed project. This repository contains recording adapters, integration metadata and newly authored synthetic fixtures, not a vendored Artex source tree. Importing an archive does not make its contents Apache-2.0 licensed.

The inspected Artex English checkout contains an AGPL-3.0 root license and a separate MIT frontend notice crediting Mohammed Arham Khan (2024). Any future redistribution of Artex source or frontend material requires the applicable original licenses and notices; the AgentStoryline license is not a replacement.

## Audio and generated recordings

Notification audio is optional and supplied by the user with `--sound`; the renderer may embed it in exported HTML and recordings. Without a supplied file, the viewer can generate a notification tone in the browser.

The previously used `mixkit-long-pop-2358.wav` is not included in the source distribution. Mixkit's Sound Effects Free License prohibits redistributing the item in a tool/template or with source files. Do not include it in a source release or public demo recording without separately establishing that the intended distribution is permitted. Public demos should use the generated tone or separately cleared audio.

Source: https://mixkit.co/license/modal/sfxFree/ (reviewed 2026-10-18).

## Names and compatibility identifiers

Provider and product names identify interoperability; they do not imply endorsement. Legacy `traceforge` format identifiers, headers and file suffixes remain for compatibility. No rights to third-party trademarks are granted by this project's license.

## Review scope

This inventory covers the current source tree and documented fixture provenance. It does not certify Git history, ignored local recordings, previously published screenshots, or future release artifacts. Those require their own public-release review.
