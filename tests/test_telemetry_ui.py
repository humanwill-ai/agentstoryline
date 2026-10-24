"""Synthetic checks for display-only telemetry selection and portable ownership."""
import json
from pathlib import Path
import subprocess
import unittest


class TelemetryUITests(unittest.TestCase):
    def test_view_selection_preserves_authoritative_primary_and_attachment(self):
        source = (Path(__file__).resolve().parents[1] / 'web' / 'flow.js').read_text()
        function = source.split('function telemetryDisplayPayload(', 1)[1].split('\nfunction initialize(', 1)[0]
        script = 'function telemetryDisplayPayload(' + function + '''
const primary={id:'artex'},orca={id:'orca'},payload={bundle:{manifest:{run_id:'a'},events:[primary]},telemetry:{bundle:{manifest:{run_id:'o'},events:[orca]}},explanations:{annotations:[{message_id:'primary',text:'Saved description'}]},flow:{messages:[{id:'primary'}]},telemetry_views:{model:{flow:{messages:[{id:'model'}]}},both:{flow:{messages:[{id:'both'}]}}}};
const before=JSON.stringify(payload),model=telemetryDisplayPayload(payload,'model'),both=telemetryDisplayPayload(payload,'both');
console.log(JSON.stringify({unchanged:JSON.stringify(payload)===before,story:telemetryDisplayPayload(payload,'story')===payload,model:model.bundle===payload.telemetry.bundle,both:both.bundle.events.map(e=>e.id),primary:payload.flow.messages[0].id,bothExplanations:both.explanations===payload.explanations,modelExplanations:model.explanations===undefined}));
'''
        value = json.loads(subprocess.check_output(['node', '-e', script], text=True))
        self.assertEqual(value, {'unchanged': True, 'story': True, 'model': True, 'both': ['artex', 'orca'], 'primary': 'primary', 'bothExplanations': True, 'modelExplanations': True})

    def test_storyline_group_opens_matched_evidence_from_source_activity(self):
        source = (Path(__file__).resolve().parents[1] / 'web' / 'flow.js').read_text()
        function = 'function inspectStoryActivity(' + source.split('function inspectStoryActivity(', 1)[1].split('function appendStoryRow(', 1)[0]
        harness = """
let selectedActivity,selectedCorrelation,captured=[];
const drawDependencies=()=>{},highlightCorrelationClips=()=>{},inspect=()=>{},inspectEvent=()=>{};
const activities=[{id:'a',event_ids:['artex-request','artex-response']}],messages=[],chatEntities=new Map();
const n=()=>({append(){}}),open=n,section=n,btn=n;
const appendTelemetryEvidence=(box,ids)=>captured.push(...ids);
"""
        script = harness + function + "inspectStoryActivity({id:'group',source_activity_ids:['a']});console.log(JSON.stringify(captured));"
        value = json.loads(subprocess.check_output(['node', '-e', script], text=True))
        self.assertEqual(value, ['artex-request', 'artex-response'])
