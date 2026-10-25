# Improve descriptions: API setup and troubleshooting

Improve descriptions adds short, evidence-linked explanations to the recording
currently loaded in AgentStoryline. It can optionally suggest milestones. It
does not change the source recording or execute its recorded tools.

## Start the local viewer

From the repository root, for example:

```bash
python3 agentstoryline.py viewer examples/artex-local-inspection.tar --state-dir work/viewer-state
```

Open the local URL printed in the terminal. Use **Import recording** if you want
to load another recording. Generation requires this running local service;
opening an exported HTML file directly supports viewing, not API generation.

With Artex and OrcaRouter telemetry attached, choose **View → Story** first.
The Improve descriptions button is disabled in **Model telemetry** and **Both**.
Generation targets the Artex recording; its explanations also appear in Both.
An independently imported Orca recording can use its own explanation workflow.

## Configure an API provider

Obtain an API key from the provider or gateway you intend to use, with access to
your chosen model and sufficient quota. AgentStoryline does not create keys or
reuse a key configured in Artex. A recording's model name does not automatically
select the model used to explain it.

1. Open **Improve descriptions** and change **Use** from **Offline / Codex session** to **API**.
2. Paste the key into **API key**. Enter the key itself, without `Bearer `.
3. Enter the provider's exact model identifier in **Model**, including any required provider prefix. There is no model discovery dropdown.
4. Under **Optional details**, check **Provider / gateway endpoint URL** before generating.

| Setting | What to enter |
| --- | --- |
| Default endpoint | `https://api.openai.com/v1`, if using a key and model available through that endpoint. |
| Compatible gateway | Its documented API base URL. AgentStoryline appends `/chat/completions`. Keep any required base-path prefix. |
| Local compatible server | A loopback HTTP base URL, such as `http://127.0.0.1:8000/v1`, only if your server exposes that API. |

The current client uses an OpenAI-compatible **Chat Completions** API, bearer
authentication, `response_format: {"type": "json_object"}` and
`max_completion_tokens`. The chosen endpoint/model must support these fields
and return JSON content in a Chat Completions response. A provider offering some
OpenAI-compatible endpoints is not a guarantee of compatibility with this
request. Native APIs with a different request shape or extra required custom
authentication headers are not configurable in this UI.

Remote endpoints require HTTPS; HTTP is accepted only for loopback hosts. Do
not include credentials, query parameters or fragments in the endpoint URL.
Redirects are not followed. Gateway and model support must be checked against
your provider's documentation; no paid provider call is made merely by opening
this dialog or previewing the eligible messages.

### Optional environment key

For a locally managed setup, the viewer service also reads
`TRACEFORGE_EXPLANATION_API_KEY` from its process environment. Set it through
your preferred local secret-management method before starting the viewer, then
leave **API key** blank. A key entered in the dialog takes precedence. The
service does not automatically read `OPENAI_API_KEY` or an `.env` file for this
setting. The environment key must belong to the endpoint you select.

## Generate and review

Keep **Scope → Missing descriptions only** for an initial pass. The preview
shows eligible message count, batches and context size; it is not a price
estimate. A recording can require multiple provider requests.

Optionally check **Suggest milestones (AI-inferred)** before clicking
**Generate descriptions**. When generation completes, close the dialog and
choose **Text → Explained**. The Original view remains available for comparison.
Milestone cards appear as their messages are revealed during playback or seeking;
AI milestones are visible only in Explained mode and may legitimately be absent
when the recorded evidence does not support a suggestion.

With Suggest milestones checked, **Missing descriptions only** also selects
messages whose saved annotation has no milestone field, even if their description
already exists. A previous assessment that returned an empty milestone list
counts as completed. To reassess those messages, use **Optional details → Scope**
to regenerate the relevant recorded interval or all descriptions. Regeneration
can incur additional provider charges.

## Key handling, recorded data and saved results

Selected messages, actor labels and bounded recorded evidence are sent to the
configured provider. Context preparation applies redaction, but that is not a
guarantee that every sensitive value is removed. Choose an appropriate provider
for your recording and review the generation scope before sending it.

The key field is cleared on submission, closing the dialog and switching modes.
The local service retains the generation configuration, including the key, in
process memory so it can complete batches and retry. Closing the dialog does
not erase the running service's copy. Stop the service to end that process;
an environment key remains available to future processes until you remove it
from their environment. AgentStoryline does not write the supplied key into
saved annotations, recordings or exported HTML.

Successful annotations are saved in the local state directory, separately from
the original evidence, with revision history. Use **Export recording**, or
**Improve descriptions → Optional details → Save enriched recording**, for a
portable copy containing the annotations. **Download HTML** saves an offline
viewer. Reimporting a raw Artex/Orca archive is not a substitute for reopening
the enriched recording; the raw archive does not contain these annotations.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Improve descriptions is disabled with telemetry attached | Switch View to Story. Generation is not offered from Model telemetry or Both. |
| No connection to the explanation service | Start `viewer` and use its printed local URL instead of a standalone HTML file. |
| Generate descriptions is disabled | Select API, wait for the preview, supply a model and endpoint, and check that there are eligible messages and no active job. |
| Waiting for Codex session | Offline mode was selected. Ask your Codex session to process the pending job, or cancel it before switching to API generation. |
| No missing descriptions | Choose an interval or all descriptions under Optional details if you intend to regenerate. |
| HTTP 401 or 403 | Check the key, endpoint, account permissions and model access. A gateway may reject a valid key intended for a different endpoint. |
| HTTP 404 | Check the API base path and exact model identifier; do not append `/chat/completions` to the configured base. |
| HTTP 429 | Check provider quota, rate limits and billing, then retry explicitly when appropriate. |
| HTTP 400, invalid JSON or unsupported response | Check support for Chat Completions, JSON response mode and the request fields above; consider a compatible model or endpoint. |
| Timed out | Each provider request has a 30-second timeout. Review connectivity/provider availability, then explicitly retry. |
| No milestone cards after generation | Check that Suggest milestones was selected, Explained mode is active, and playback has reached the relevant messages. Generation does not guarantee milestones. |

**Retry failed** reuses the service's stored generation configuration for pending
targets. If you need to change the endpoint, model or key, start a new generation
with the corrected settings instead. Cancellation stops subsequent work but
does not abort the in-flight HTTP request; that batch can still finish and be
saved. It cannot undo an already sent request or its possible charge. Successfully saved
descriptions remain available after a later batch fails.

For attachment setup and source differences, see the
[Artex + Orca walkthrough](ARTEX_ORCA_WALKTHROUGH.md). For milestone provenance,
see [Milestones](MILESTONES.md).
