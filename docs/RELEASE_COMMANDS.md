# Local release-candidate commands

These commands prepare and check a local source snapshot. They do not authorize
publication, a commit, push, tag, repository-visibility change, upload, deployment,
or release workflow. See the [publication checklist](PUBLICATION_CHECKLIST.md),
[release scope](RELEASE_SCOPE_V0_1.md), and [source audit](PUBLIC_SOURCE_AUDIT.md).

Use Python 3.10+ from the checkout. Choose a **new** destination outside the
checkout, with an existing parent; on macOS use `/private/tmp` rather than its
symlink alias. Replace the sample destination consistently for each new build.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/build_public_source.py /private/tmp/agentstoryline-candidate
PYTHONDONTWRITEBYTECODE=1 python3 scripts/verify_release_candidate.py /private/tmp/agentstoryline-candidate
```

The verifier reads the snapshot without executing its code or recorded actions,
starting services, making provider calls, or fetching links. It checks the exact
manifest schema, required release files, the reviewed source allowlist, counts,
byte sizes, every SHA-256, and the aggregate canonical file-list SHA-256. It also
checks local Markdown targets and Markdown heading/explicit HTML anchors, plus
local HTML `href`/`src` targets in Markdown. External URLs are left unchecked.
Markdown parsing supports the repository's ordinary inline, reference, and HTML
links; it is not a full CommonMark renderer or a browser behavior check.

The tree must be clean: extra files, extra directories, runtime caches, prohibited
paths, symlinks, and special files fail verification. There is no cleanup or
ignore-caches option. Run checks without writing bytecode so the clean snapshot
can be verified again. The verifier does not mutate or remove failed candidates.

Run the focused synthetic packaging checks from the checkout:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_public_source tests.test_release_candidate tests.test_orcareplay_examples -v
```

Run the complete existing offline/synthetic suite from the staged candidate:

```sh
cd /private/tmp/agentstoryline-candidate
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 scripts/verify_release_candidate.py .
```

The test command is a separate explicit operation. The verifier never runs it.
Record its results and the final manifest hash outside the candidate so the
record does not change the bytes being identified. A passing check establishes
local consistency, not authorship, rights clearance, complete secret detection,
hostile concurrent-write protection, or publication approval. The
[release validation](RELEASE_VALIDATION.md) records validation and open work.

The first-release source candidate includes the experimental read-only
OrcaReplay adapter and its newly authored synthetic directory/native-ZIP/plain-tar example.
Its example checks verify deterministic bytes, unchanged evidence hashes,
normalized import and portable reload without running recorded operations.
See the [OrcaReplay profile](ORCAREPLAY_ADAPTER.md).
