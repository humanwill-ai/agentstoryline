# Security policy

AgentStoryline v0.1 is a local-first agent-trace viewer. Import and playback must
treat recordings, messages, scripts and retained files as untrusted, inert data.
Procedural execution is outside this release's scope. Artex import is supported;
Codex native rollout, offline OTLP protobuf-JSON and OrcaReplay imports are experimental. See
the [release scope](docs/RELEASE_SCOPE_V0_1.md).

## Report a vulnerability privately

**Do not open a public issue, pull request or discussion containing sensitive
vulnerability details, recordings or credentials.**

GitHub private vulnerability reporting is disabled by the owner's decision.
No private reporting channel or security contact email is currently designated.
Do not submit sensitive details through public issues or pull requests; retain
them privately until maintainers establish a private channel. No response-time
commitment is made.

Include the affected version or source revision, input format, operating system,
reproduction steps, expected and observed behavior, and the potential impact.
Prefer the smallest newly authored synthetic input that reproduces the issue.
Describe which boundary is crossed and any configuration required. Do not send
live credentials, private recordings, personal data or unrelated source files.
Coordinate disclosure privately with maintainers while a report is assessed.

## Security boundaries and relevant reports

Useful reports include unsafe archive/path handling, access to files outside a
retained import, source-content execution or browser injection, unintended
network/provider requests, bypasses of explicit approval or scope controls, and
unintended exposure of local state or credentials. Experimental features can
still have security defects; label the affected feature and configuration.

Use synthetic inputs and a local environment you control. Do not access another
person's sessions, send paid model requests, test internet targets or publish
real evidence to demonstrate a defect. A security report does not authorize
those actions.

## Operational limits

The local service is intended for loopback use. Hosted or public-network
deployment and multi-user isolation are outside v0.1. Keep recordings and state
private and review retained original files before distributing any export.
Normal JSON/HTML export preserves content; it is not redaction. Experimental
sharing offers a reviewed derivative, but automatic redaction cannot guarantee
removal of every sensitive value.

Import and playback do not start description generation. Explicit API generation
sends selected excerpts to the configured provider and may incur charges.
Source labels and checksums do not authenticate a recording's origin, and
validated descriptions remain interpretations rather than verified facts.

## Version and maintenance status

v0.1.0 is the initial viewer release, published on 2026-10-18. No independent
security review is implied by this policy. There is no maintenance commitment
for older prototypes, experimental execution modules or independent source-agent
projects. Report issues against the current code with the precise revision and
feature identified; maintainers will assess applicability and any remediation.
Current checks and their limits are recorded in
[release validation](docs/RELEASE_VALIDATION.md).
