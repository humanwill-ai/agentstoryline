# Sanitized OrcaTest 1 demo

This explicitly authorized demo contains a real Artex run captured against an authorized local synthetic fixture, plus four associated OrcaRouter gateway captures. It is not an invented synthetic transcript. The native Artex archive retains all 602 events. The original captures are not distributed.

Follow the [illustrated walkthrough](../../docs/ARTEX_ORCA_WALKTHROUGH.md) to import Artex first, optionally attach all four Orca files, then compare **Story**, **Model telemetry** and **Both**.

## Files

- `artex/OrcaTest-task-1-native.tar.zst`: sanitized native Artex archive.
- `orca/run_36524fd8f6747fc2712506d0.orca.zip`
- `orca/run_5ec32a87c10ed8175272bfe3.orca.zip`
- `orca/run_78e32c79f75c04aba773d673.orca.zip`
- `orca/run_91e75b3261e20f42ca1f2f44.orca.zip`

The four Orca files contain 33 model exchanges. Attaching them to this Artex archive yields 29 matched exchanges, four Orca-only exchanges, zero ambiguous exchanges, zero conflicts and one unresolved session. An unresolved session is retained without an invented task-membership claim.

## Sanitization and provenance

The public IP was replaced with documentation address `192.0.2.1`, and the user home path with `/Users/demo/`. Credentials belonging to the local synthetic fixture are deliberately retained with explicit owner authorization so the captured evidence remains readable. They are demo values, not production credentials. The sanitized files are derivatives; they do not claim byte identity with the original captures.

This demo is an explicit exception to the repository rule excluding real recordings and credentials from Git. That exception applies only to these reviewed sanitized derivatives and their synthetic fixture credentials; private captures and original evidence remain outside the repository. Do not infer that other recordings are safe to publish.

The main README playback video uses a different recording. Importing and playing this demo uses local files only, makes no model calls and executes no recorded commands or tools. Native `.tar.zst` import requires `zstd` and the local viewer service.
