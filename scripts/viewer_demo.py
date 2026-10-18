#!/usr/bin/env python3
"""Render an AgentStoryline demo from newly authored synthetic Artex records; no target or actions execute."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from adapters.artex.importer import import_artex
from analysis.narrative import build_observed_timeline
from web.render import render_timeline


def run_demo(destination):
    output = Path(destination).absolute()
    output.mkdir(parents=True, exist_ok=False)
    bundle = import_artex(ROOT / 'tests/fixtures/artex/token_session', output / 'bundle')
    render_timeline(bundle, build_observed_timeline(bundle), output / 'timeline.html',
                    recording_output=output / 'recording.traceforge.json')
    result = {'synthetic': True, 'execution': 'not_requested',
              'fixture_origin': 'newly authored synthetic Artex records; not an actual agent run',
              'html': str(output / 'timeline.html'),
              'recording': str(output / 'recording.traceforge.json'), 'events': len(bundle['events'])}
    (output / 'demo-result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', help='New directory for viewer artifacts')
    print(json.dumps(run_demo(parser.parse_args().output), indent=2))
