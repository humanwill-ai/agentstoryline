"""New synthetic regressions for native Artex relationship presentation."""
import json
from pathlib import Path
import subprocess
import unittest

SOURCE = (Path(__file__).resolve().parents[1] / 'web/flow.js').read_text()

def helper(name):
    start = SOURCE.index('function ' + name + '(')
    return SOURCE[start:SOURCE.index('\nfunction ', start + 1)]

def node(script):
    return json.loads(subprocess.check_output(['node', '-e', script], text=True))

class ArtexRelationshipUITests(unittest.TestCase):
    def test_projection_requires_native_artex_recorded_semantics(self):
        item = dict(id='r1', kind='task_structure', provenance='source_recorded', source_event_id='a', target_event_id='b')
        cases = []
        for adapter in ['artex', 'orcareplay', 'codex', 'otlp', 'unknown']:
            cases.append({'bundle': {'manifest': {'source': {'adapter': adapter}}}, 'storyline': {'artex_relationships': {'version': 1, 'items': [item]}}})
        script = helper('declaredAdapter') + helper('artexRecordedRelationships')
        script += '\nconst cases=' + json.dumps(cases) + ';const native=cases[0];const before=JSON.stringify(native);'
        script += "const result=cases.map(p=>artexRecordedRelationships(p).length);native.storyline.artex_relationships.items.push({...native.storyline.artex_relationships.items[0],provenance:'content_inferred'});result.push(artexRecordedRelationships(native).length);native.storyline.artex_relationships.version=2;result.push(artexRecordedRelationships(native).length);process.stdout.write(JSON.stringify(result));"
        self.assertEqual(node(script), [1, 0, 0, 0, 0, 1, 0])

    def test_arrow_filter_requires_projected_identity_and_kind_toggle(self):
        script = helper('artexRelationshipVisible') + """
const items=[{id:'r1',kind:'task_structure'}],edge={kind:'artex_relationship',relationship_id:'r1',relationship_kind:'task_structure'},on={task_structure:true,evidence_binding:true};
process.stdout.write(JSON.stringify([artexRelationshipVisible(edge,items,on),artexRelationshipVisible(edge,items,{task_structure:false}),artexRelationshipVisible({...edge,relationship_id:'unknown'},items,on),artexRelationshipVisible({...edge,relationship_kind:'evidence_binding'},items,on),artexRelationshipVisible({...edge,kind:'content_correlation'},items,on)]));
"""
        self.assertEqual(node(script), [True, False, False, False, False])

    def test_draw_suppresses_collapsed_group_self_arrows(self):
        script = helper('artexRelationshipVisible') + helper('linkLayerVisible') + helper('drawDependencies') + """
const isBothCorrelations=false,relationshipLayer='all';let added=0;const clip={dataset:{id:'group',sourceActivityIds:'["a","b"]'}},host={getBoundingClientRect:()=>({width:100,height:100}),querySelectorAll:()=>[clip]},$=()=>host,dependencySvg={replaceChildren(){},setAttribute(){},style:{},append(){added++}},isOrcaCollection=false,selectedCorrelation=null,selectedActivity=null,artexRelationships=[{id:'r1',kind:'task_structure'}],relationshipKinds={task_structure:true},story={edges:[{kind:'artex_relationship',relationship_id:'r1',relationship_kind:'task_structure',source_activity_id:'a',target_activity_id:'b'}]};
drawDependencies();process.stdout.write(JSON.stringify(added));
"""
        self.assertEqual(node(script), 0)

    def test_inspector_and_controls_preserve_source_claims(self):
        inspector = helper('inspectArtexRelationship')
        self.assertIn('item.evidence_event_ids', inspector)
        self.assertIn("section('Exact source record · '+id,p.evidence_text?.[id]?.event??event)", inspector)
        self.assertIn('does not establish an execution dependency', inspector)
        self.assertIn("const relationshipKinds={task_structure:true,evidence_binding:true}", SOURCE)
        self.assertIn('if(!isArtexRelationships)return;', helper('buildArtexRelationshipStrip'))
        self.assertIn("if(declaredAdapter(payload)!=='orcareplay'", helper('orcaCollectionCorrelations'))

    def test_chip_context_is_bounded_without_losing_tooltip_endpoints(self):
        script = helper('artexRelationshipContext') + "const item={source_label:'Task '+ 'x'.repeat(90),target_label:'Evidence '+ 'y'.repeat(90)};process.stdout.write(JSON.stringify({context:artexRelationshipContext(item),item}));"
        result = node(script)
        self.assertLessEqual(len(result['context']['text']), 107)
        self.assertEqual(result['context']['title'], result['item']['source_label'] + ' → ' + result['item']['target_label'])
        self.assertIn('display_anchor_basis', helper('inspectArtexRelationship'))
        self.assertIn('item.source_event_id,item.target_event_id', helper('inspectArtexRelationship'))
