"""Render recordings without fetching remote assets or executing recorded content."""
import json
import base64
import io
import wave
from pathlib import Path

from web.storyline import build_storyline
from web.branding import embed_brand_assets
from web.theme import apply_default_flow_theme


def build_payload(bundle, narrative, *, replay_plan=None, view="flow", notification_sound=None):
    if view not in ("flow", "evidence"):
        raise ValueError("view must be flow or evidence")
    sound_uri = None
    if notification_sound is not None:
        sound_path = Path(notification_sound)
        # Embed a bounded local PCM WAV, never an executable or fetched asset.
        with sound_path.open("rb") as sound_file:
            raw_sound = sound_file.read(2 * 1024 * 1024 + 1)
        if len(raw_sound) > 2 * 1024 * 1024:
            raise ValueError("notification sound must be a WAV smaller than 2 MiB")
        try:
            with wave.open(io.BytesIO(raw_sound), "rb") as audio:
                if audio.getnframes() / audio.getframerate() > 5:
                    raise ValueError("notification sound must be at most 5 seconds")
                if not audio.getnframes():
                    raise ValueError("notification sound must contain audio")
        except (wave.Error, EOFError) as exc:
            raise ValueError("notification sound must be a valid PCM WAV") from exc
        sound_uri = "data:audio/wav;base64," + base64.b64encode(raw_sound).decode("ascii")
    storyline = build_storyline(bundle, narrative)
    replay_view = None
    if replay_plan is not None:
        from web.replay_view import build_replay_view
        replay_view = build_replay_view(bundle, replay_plan, storyline)
        storyline["edges"].extend(replay_view["edges"])
    # Browser JSON numbers cannot represent every JSON integer exactly. Keep
    # separately serialized text for evidence display; JS only indexes events.
    payload = {"bundle": bundle, "narrative": narrative,
               "storyline": storyline, "replay": replay_view,
               "replay_steps_text": {step["id"]: json.dumps(step, indent=2, ensure_ascii=False)
                                     for step in (replay_plan or {}).get("steps", [])},
               "evidence_text": {event["id"]: {"event": json.dumps(event, indent=2, ensure_ascii=False),
                                                  "data": json.dumps(event.get("data", {}), indent=2, ensure_ascii=False),
                                                  "sequence": str(event["sequence"])}
                                 for event in bundle.get("events", [])},
               "manifest_text": json.dumps(bundle.get("manifest", {}), indent=2, ensure_ascii=False)}
    if view == "flow":
        from web.flow import build_flow
        from web.pacing import build_smart_pacing
        from web.time_axis import build_story_axis
        payload["flow"] = build_flow(bundle, narrative, storyline)
        payload["flow"]["story_axis"] = build_story_axis(storyline, payload["flow"])
        payload["flow"]["pacing"] = build_smart_pacing(payload["flow"])
        payload["notification_sound"] = sound_uri
    return payload


def render_timeline(bundle, narrative, destination, *, replay_plan=None, view="flow", notification_sound=None, recording_output=None, replay_scripts=()):
    payload = build_payload(bundle, narrative, replay_plan=replay_plan, view=view, notification_sound=notification_sound)
    return render_payload(payload, destination, view=view, recording_output=recording_output, replay_scripts=replay_scripts)


def render_payload(payload, destination, *, view="flow", recording_output=None, replay_scripts=(), attachments=()):
    """Render a prepared display payload without rebuilding its preserved evidence."""
    if view not in ("flow", "evidence"):
        raise ValueError("view must be flow or evidence")
    if 'telemetry' in payload:
        from web.telemetry import build_telemetry_views
        payload = build_telemetry_views(payload)
    path = Path(destination)
    outputs = [path] + ([Path(recording_output)] if recording_output is not None else [])
    if len({output.resolve() for output in outputs}) != len(outputs):
        raise ValueError("HTML and portable recording destinations must be different")
    for output in outputs:
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"output already exists: {output}")
    if recording_output is not None:
        if view != "flow":
            raise ValueError("portable recordings require --view flow")
        from web.recording import write_recording
        write_recording(payload, recording_output, replay_scripts=replay_scripts, attachments=attachments)
    elif replay_scripts or attachments:
        raise ValueError("--replay-script requires --recording-output")
    path = Path(destination)
    data = json.dumps(payload, ensure_ascii=False)
    for char, replacement in [("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")]:
        data = data.replace(char, replacement)
    assets = Path(__file__).parent
    template_name = "flow" if view == "flow" else "timeline"
    asset_name = "flow" if view == "flow" else "storyline"
    template = (assets / (template_name + ".html")).read_text(encoding="utf-8")
    if view == "flow":
        template = apply_default_flow_theme(template)
    # Bundle trusted local assets before inserting untrusted evidence. Content
    # containing a placeholder must never trigger a second replacement pass.
    template = embed_brand_assets(template)
    template = template.replace("__TRACE_CSS__", (assets / (asset_name + ".css")).read_text(encoding="utf-8"))
    template = template.replace("__TRACE_JS__", (assets / (asset_name + ".js")).read_text(encoding="utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(template.replace("__TRACE_DATA__", data))
    return path
