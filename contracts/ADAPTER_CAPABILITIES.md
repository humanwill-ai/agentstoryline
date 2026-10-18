# Recording adapter capabilities

The common bundle contract supports partial recordings. Adapters must report what their source actually establishes; using the same event names does not establish equivalent capture coverage or portable execution.

| Capability | Artex recording export v1 | Codex native rollout adapter |
|---|---|---|
| Source | Authenticated read-only task snapshot, native archive format 1–3 compatibility | Bounded native JSONL rollout or explicitly selected native record subset |
| Model evidence | Normalized recorded request/response, structured content, optional raw transport bodies | Visible assistant transcript items and recorded turn/model metadata; no reconstructed provider request |
| Tool input/output | Structured harness input/result when recording enabled; inherited activity otherwise | Native function/custom-tool invocation and output records |
| Identity | Explicit logical model-call/session/tool-use IDs where observed | Native session/call IDs; recorded context identity is not provider attestation |
| Dependencies | Explicit model/tool associations when unambiguous; other relations identified as inferred or absent | Unambiguous call/output pairing only; no inferred model-request ancestry |
| Timing | Observed request/response/activity boundaries; raw source timestamps retained | Native rollout record timestamps; file order retained separately |
| Completeness | Always partial; omitted files/tables and recording gaps declared | Always partial; selected coverage, missing provider boundaries, and excluded record types declared |
| Execution | Separate bounded HTTP replay plan | No Codex tool execution adapter; transcript commands are data |

A new Codex model/tool exchange validates a second actual source format without changing bundle schema 0.1. It does not establish support for every Codex release, every resumed/concatenated rollout, or another security agent's tool semantics. See the adapter's documented supported native shapes and limitations.

Keep source evidence outside Git. Tests use newly authored source-shaped records; actual runtime exports/transcript excerpts remain separate private artifacts. Neither adapter executes source commands or automatically sends source content to a model.
