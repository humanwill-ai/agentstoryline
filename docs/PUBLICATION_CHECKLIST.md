# v0.1.0 publication checklist

This is the handoff from step 5 release-material preparation to step 6
owner-controlled publication work. **Nothing has been released.** Preparation
does not itself authorize commits, pushes, tags, visibility changes, releases,
deployment or release workflows. Keep this repository private until the owner
explicitly authorizes the applicable action and destination.

## Current owner decision — 2026-10-18

Local commits and preparation through step 6 are authorized. Publication remains
on hold for a separate owner confirmation. The current second-browser check is
waived for v0.1.0, not passed. The release will use an audited source snapshot
with a clean Git root and a GitHub noreply commit email; private development
history will not be shared. Private vulnerability reporting must be enabled at
publication. The existing public teaser is not a source-code release.

## Required before publication

- [x] **Waived by the owner for v0.1.0; not tested.** Complete a visual and keyboard interaction pass in a **current second
  browser** (Safari or Firefox), using only synthetic examples. Record browser
  and OS versions, viewport, tested interactions and findings in
  [release validation](RELEASE_VALIDATION.md). Verify import, playback/pause,
  seeking, theme/text menus, evidence opening/closing, focus navigation and
  portable export/reimport. Fix material failures and recheck them. An obsolete
  browser or automated DOM test does not satisfy this item.
- [x] Review the final [release scope](RELEASE_SCOPE_V0_1.md),
  [release notes](RELEASE_NOTES_V0_1.md), [changelog](../CHANGELOG.md), README,
  examples and media for consistent supported/experimental/out-of-scope claims.
  Confirm all release-material relative links resolve inside the staged package.
- [x] Review source, fixtures, mascot provenance/notices and every distributed
  screenshot/video against the [public source audit](PUBLIC_SOURCE_AUDIT.md).
  Confirm screenshots use authored synthetic records and the reviewed video shows
  an authorized isolated synthetic-target run with permitted audio only. Keep real
  recordings, credentials, research, private state and integration pins excluded.
- [x] Build a new local snapshot with `scripts/build_public_source.py` to a fresh
  destination outside the checkout. Inspect its exact
  `PUBLIC_SOURCE_MANIFEST.json`, omitted optional files and per-file hashes.
  Confirm LICENSE, notices, release materials, examples and intended media exist.
  A directory copy or Git-tracked status is not an approved package inventory.
- [x] Rebuild to a second fresh destination and confirm identical manifest and
  file bytes. Record the final aggregate package hash outside the package.
- [x] Run `python3 -m unittest discover -s tests -v` in the exact final snapshot;
  record command, Python/platform versions, test totals and remaining warnings
  in release validation. Recheck relevant behavior after any further changes.
- [x] Run the documented synthetic quickstart from the final snapshot with a
  fresh ignored output/state directory. Open the viewer and standalone HTML,
  export/reimport portable JSON, and verify the copied example inputs. Do not
  make provider calls or execute recorded operations for this check.
- [x] Inspect the final diff and artifact inventory. Confirm authorization,
  approval and local scope controls remain intact; hidden experimental controls
  remain hidden; publication claims match observed validation.

## Owner decision and step 6 execution

- [ ] Give the owner the exact candidate snapshot hash, proposed `v0.1.0` tag,
  intended repository/destination, release text and assets, final validation
  results, and any unresolved limitations for review.
- [ ] Obtain explicit authorization for each intended external or Git action:
  commit/push, tagging, release publication, visibility change, deployment or
  workflow enablement. Record the authorized scope before acting. No permission
  for one action implies permission for the others.
- [ ] If publishing repository history is proposed, complete a separate
  history/secret/rights audit first. Snapshot review does not cover private Git
  history; do not substitute the development checkout for the reviewed package.
- [ ] Execute only the authorized step 6 actions against the reviewed candidate.
  Verify artifact hashes and destination visibility, preserve licenses/notices
  and source identifiers, and report the actual resulting URLs and version.
- [ ] Update prepared/unreleased wording and dates only when the corresponding
  release action has occurred, under its authorization. Record the actual
  outcome and any remaining work; do not mark unchecked gates complete by
  inference.

Until these gates are satisfied and the owner authorizes publication, the
candidate remains private and unreleased. Existing test counts, prior media
publication or repository-setup permission do not authorize a launch.
