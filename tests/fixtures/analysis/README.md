# Newly authored interpretation fixture

`artex-token-narrative.json` is development-worker-authored interpretation of
the newly authored synthetic `artex/token_session` export, after importing it
through the Artex adapter. It is the raw offline response content accepted by
`analysis.narrative.import_analysis_response`; method/model/provider provenance
is added at import time. Its two stages explain session acquisition and the
protected request with links to actual imported event IDs.

This fixture is not output from an automated provider integration, a paid model
call, an evaluation result, a historic Artex run, or independent proof that the
target behaved as reported. It was written during development by the analysis
worker and checked for valid evidence references. It distinguishes source
reports from independently observed execution and qualifies the inferred
cross-request token dependency. Treat its text as interpretation, never as
execution authority or as validation of the narrative's factual claims.
