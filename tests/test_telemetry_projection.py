"""Synthetic-only checks of supplemental presentation projections."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from analysis.narrative import build_observed_timeline
from analysis.telemetry import build_telemetry_attachment
from analysis.explanation_workflow import load_portable
from web.render import build_payload
from web.telemetry import build_telemetry_views


def recording(prefix, *, second=0, orphan=False):
    start = '2026-01-01T00:00:%02dZ' % second
    manifest = {'schema_version': '0.1', 'run_id': prefix,
                'agent': {'name': 'Synthetic', 'version': '1'}, 'started_at': start,
                'completeness': {'status': 'complete', 'gaps': []},
                'source': {'adapter': 'artex' if prefix == 'a' else 'orcareplay'}}
    events = []
    for n, prompt in enumerate(['shared'] + (['orphan'] if orphan else [])):
        q = prefix + '-q%d' % n
        for kind in ['model.request', 'model.response']:
            data = {'source_session': {'id': prefix, 'sequence': n * 2}}
            data['request' if kind.endswith('request') else 'response'] = (
                {'messages': [{'role': 'user', 'content': prompt}]} if kind.endswith('request') else {'text': 'answer-' + prompt})
            e = {'schema_version': '0.1', 'run_id': prefix, 'id': q if kind.endswith('request') else prefix + '-a%d' % n,
                 'kind': kind, 'timestamp': '2026-01-01T00:00:%02dZ' % (second + len(events)),
                 'sequence': len(events), 'data': data}
            if kind.endswith('response'):
                e['parent_id'] = q
            events.append(e)
    return {'manifest': manifest, 'events': events}


class TelemetryProjectionTests(unittest.TestCase):
    def attached(self):
        primary, supplement = recording('a', second=10), recording('o', orphan=True)
        payload = build_payload(primary, build_observed_timeline(primary))
        payload['telemetry'] = build_telemetry_attachment(primary, supplement)
        return payload

    def test_other_imports_are_noop(self):
        payload = {'unrelated': True}
        self.assertIs(build_telemetry_views(payload), payload)

    def test_sources_unchanged_matched_exchange_once_orphan_visible(self):
        payload = self.attached()
        original = copy.deepcopy(payload)
        result = build_telemetry_views(payload)
        self.assertEqual(payload, original)
        events = [eid for m in result['telemetry_views']['both']['flow']['messages'] for eid in m['event_ids']]
        self.assertIn('a-q0', events)
        self.assertNotIn('o-q0', events)
        self.assertIn('o-q1', events)
        self.assertIn('o-a1', events)
        for view in result['telemetry_views'].values():
            flow, story = view['flow'], view['storyline']
            for rows in [flow['messages'], story['activities'], story['lanes'], flow['entities']]:
                self.assertEqual(len(rows), len({row['id'] for row in rows}))
            activities = {a['id'] for a in story['activities']}
            lanes = {a['id'] for a in story['lanes']}
            for m in flow['messages']:
                self.assertIn(m['activity_id'], activities)
                self.assertIn(m['lane_id'], lanes)

    def test_display_projection_preserves_source_references(self):
        result = build_telemetry_views(self.attached())
        messages = result['telemetry_views']['model']['flow']['messages']
        response = next(m for m in messages if 'o-a1' in m['event_ids'])
        self.assertEqual(response['parent_id'], 'o-q1')

    def test_recorded_offsets_share_common_clock(self):
        result = build_telemetry_views(self.attached())
        both = result['telemetry_views']['both']
        self.assertEqual(both['storyline']['origin_timestamp'], '2026-01-01T00:00:00Z')
        by_event = {e: m for m in both['flow']['messages'] for e in m['event_ids']}
        self.assertEqual(by_event['a-q0']['recorded_offset_ms'], 10000)
        self.assertEqual(by_event['o-q1']['recorded_offset_ms'], 2000)
        chapters = {c['id']: c for c in both['storyline']['chapters']}
        for chapter in both['flow']['chapters']:
            self.assertEqual(chapter['start_ms'], chapters[chapter['id']]['start_ms'])

    def test_orca_declared_sequence_links_include_report_tools_only(self):
        payload = self.attached()
        source = payload['telemetry']['bundle']
        # Native Orca relationships are scoped sequence references, not parents.
        for event in source['events']:
            event['data']['source_record'] = {'seq': event['sequence']}
            if event['kind'] == 'model.response':
                event.pop('parent_id')
                event['data']['source_record']['causes'] = [event['sequence']-1]
                event['data']['source_causes'] = {'sequences':[event['sequence']-1]}
        for index,kind in enumerate(('tool.start','tool.end'), start=4):
            source['events'].append({'schema_version':'0.1','id':'o-tool-'+str(index),
                'kind':kind,'tool':'write_report','run_id':'o','sequence':index,
                'timestamp':'2026-01-01T00:00:0'+str(index)+'Z',
                'data':{'source_session':{'id':'o','sequence':index},
                        'source_record':{'seq':index,'causes':[index-1]}}})
        payload['telemetry'] = build_telemetry_attachment(payload['bundle'],source)
        result = build_telemetry_views(payload)
        shown = {eid for m in result['telemetry_views']['both']['flow']['messages'] for eid in m['event_ids']}
        self.assertTrue({'o-q1','o-a1','o-tool-4','o-tool-5'} <= shown)
        self.assertNotIn('o-q0',shown)

    def test_sanitized_derivative_omits_attachment_with_disclosure(self):
        from web.sharing import prepare_share
        payload = build_telemetry_views(self.attached())
        package = {'format':'traceforge-recording','version':1,'payload':payload,'attachments':[]}
        result = prepare_share(package)
        self.assertNotIn('telemetry', result['package']['payload'])
        self.assertNotIn('telemetry_views', result['package']['payload'])
        self.assertTrue(result['report']['omitted_telemetry'])
        self.assertIn('telemetry',package['payload'])

    def test_export_reopen_validates_extension(self):
        payload = self.attached()
        package = {'format': 'traceforge-recording', 'version': 1, 'payload': payload, 'attachments': []}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.json'
            path.write_text(json.dumps(package))
            reopened = load_portable(path)
            self.assertEqual(reopened['payload']['telemetry'], payload['telemetry'])
            self.assertEqual(build_telemetry_views(reopened['payload'])['telemetry']['summary']['matched_exchanges'], 1)


if __name__ == '__main__':
    unittest.main()
