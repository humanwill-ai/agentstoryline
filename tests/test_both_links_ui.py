"""New synthetic regressions for attached Orca correlations in Both view."""
import json
import subprocess
import unittest
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / 'web/flow.js').read_text()


def helper(name):
    start = SOURCE.index('function ' + name + '(')
    return SOURCE[start:SOURCE.index('\nfunction ', start + 1)]


def node(script):
    return json.loads(subprocess.check_output(['node', '-e', script], text=True))


class BothLinksUITests(unittest.TestCase):
    def test_extension_requires_both_artex_and_attached_projection(self):
        item = dict(id='c1', provenance='content_inferred', kind='tool_context_reuse',
                    source_event_id='orca-a', target_event_id='orca-b', display_status='mapped',
                    display_source_event_id='artex-a', display_target_event_id='artex-b')
        payload = dict(bundle=dict(manifest=dict(source=dict(adapter='artex'))),
                       telemetry=dict(bundle=dict(events=[])),
                       storyline=dict(telemetry_correlations=dict(version=1, items=[item])))
        script = helper('declaredAdapter') + helper('bothTelemetryCorrelations')
        script += '\nconst p=' + json.dumps(payload) + ';const before=JSON.stringify(p);'
        script += '''const result=['story','model','both'].map(mode=>bothTelemetryCorrelations(p,mode).length);
result.push(JSON.stringify(p)===before);
p.bundle.manifest.source.adapter='orcareplay';result.push(bothTelemetryCorrelations(p,'both').length);
p.bundle.manifest.source.adapter='artex';delete p.telemetry;result.push(bothTelemetryCorrelations(p,'both').length);
process.stdout.write(JSON.stringify(result));'''
        self.assertEqual(node(script), [0, 0, 1, True, 0, 0])

    def test_chat_uses_display_ids_and_never_invents_unresolved_anchors(self):
        script = helper('correlationDisplayEvents') + helper('correlationPresentationEntries')
        script += '''
const isOrcaCollection=true,layout='single',sessionFilter='',messages=[{id:'m1',event_ids:['artex-a']},{id:'m2',event_ids:['artex-b']}],visible=new Set(['m1','m2']);
const mapped={id:'c1',source_event_id:'orca-a',target_event_id:'orca-b',display_source_event_id:'artex-a',display_target_event_id:'artex-b',display_status:'mapped'};
const orcaCorrelations=[mapped,{...mapped,id:'unresolved',display_status:'unresolved'}];
const entries=messages.map(m=>({id:m.id,messages:[m]}));
process.stdout.write(JSON.stringify({entries:correlationPresentationEntries(entries).map(e=>e.id),original:correlationDisplayEvents({source_event_id:'orca-a',target_event_id:'orca-b'}),unresolved:correlationDisplayEvents(orcaCorrelations[1])}));'''
        self.assertEqual(node(script), dict(entries=['m1', 'm2', 'handoff:c1'], original=['orca-a', 'orca-b'], unresolved=[]))

    def test_match_badges_require_explicit_artex_event_ids(self):
        script = helper('matchedArtexEvents') + '''
const telemetry={matches:[{artex_request_id:'artex-a',artex_response_id:'artex-b',orca_request_id:'orca-a'}]};
process.stdout.write(JSON.stringify([matchedArtexEvents(telemetry,['artex-a']),matchedArtexEvents(telemetry,['artex-b']),matchedArtexEvents(telemetry,['orca-a']),matchedArtexEvents(telemetry,['same-name-at-same-time']),matchedArtexEvents(null,['artex-a'])]));'''
        self.assertEqual(node(script), [True, True, False, False, False])

    def test_source_session_filter_is_only_offered_for_pure_orca(self):
        # Both retains these sessions for evidence labels, but filtering would hide
        # native Artex messages without Orca session metadata.
        gate = "declaredAdapter(p)==='orcareplay'&&sessions.length>1"
        self.assertIn('if(' + gate + '){', SOURCE)
        script = helper('declaredAdapter') + """
const sessions=[{id:'s1'},{id:'s2'}];
const result=['artex','orcareplay'].map(adapter=>{const p={bundle:{manifest:{source:{adapter}}}};return GATE;});
process.stdout.write(JSON.stringify(result));
""".replace('GATE', gate)
        self.assertEqual(node(script), [False, True])

    def test_original_evidence_and_mapping_remain_inspectable(self):
        inspector = helper('inspectCorrelation')
        for text in ['inspectEvent(item.source_event_id)', 'inspectEvent(item.target_event_id)', 'item.target_evidence', 'item.display_mapping']:
            self.assertIn(text, inspector)
        strip = helper('buildHandoffStrip')
        self.assertIn("isBothCorrelations?'details':'section'", strip)
        self.assertIn('item.display_mapping?.reason', strip)
        self.assertIn('unresolved', strip)
        css = (Path(__file__).resolve().parents[1] / 'web/flow.css').read_text()
        self.assertIn('.f-both-correlations .f-dependency-path.correlation{stroke:#c98a35;stroke-dasharray:6 4}', css)
        self.assertIn('.f-dependency-path.artex.evidence_binding{stroke:#b18bce;stroke-dasharray:3 3}', css)


if __name__ == '__main__':
    unittest.main()
