"""New synthetic checks for Both owner-lane anchors and link-layer routing."""
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


class LinkLaneUITests(unittest.TestCase):
    def test_layer_filter_gates_selected_links_and_preserves_other_modes(self):
        script = helper('linkLayerVisible') + """
const edges=[{kind:'content_correlation',selected:true},{kind:'artex_relationship'},{kind:'dependency'}];
process.stdout.write(JSON.stringify(['all','artex','telemetry'].map(layer=>[true,false].map(both=>edges.map(e=>linkLayerVisible(e,both,layer))))));
"""
        self.assertEqual(node(script), [[[True]*3,[True]*3],[[False,True,True],[True]*3],[[True,False,False],[True]*3]])
        draw = helper('drawDependencies')
        self.assertLess(draw.index('!linkLayerVisible'), draw.index('e.source_activity_id!==selectedActivity'))
        self.assertIn("if(isBothCorrelations)", helper('setupStoryLaneModes'))

    def test_owner_requires_both_logical_and_matching_recorded_entity(self):
        script = helper('correlationOwnerRow') + """
const row={dataset:{laneId:'entity:agent'}},host={querySelectorAll:()=>[row]},edge={kind:'content_correlation',display_source_owner_entity_id:'entity:agent'};
process.stdout.write(JSON.stringify([correlationOwnerRow(edge,'source',host,true,'logical')===row,correlationOwnerRow(edge,'source',host,false,'logical'),correlationOwnerRow(edge,'source',host,true,'source'),correlationOwnerRow(edge,'target',host,true,'logical'),correlationOwnerRow({...edge,kind:'dependency'},'source',host,true,'logical')]));
"""
        self.assertEqual(node(script), [True,None,None,None,None])

    def test_six_routes_are_distinct_stable_bounded_and_keep_exact_endpoints(self):
        script = helper('correlationRoute') + """
const routes=Array.from({length:6},(_,i)=>correlationRoute(100,80,500,80,i,600,160));
const resized=correlationRoute(200,80,1000,80,2,1200,160);
process.stdout.write(JSON.stringify({routes,resized,repeat:correlationRoute(100,80,500,80,2,600,160)}));
"""
        result=node(script)
        self.assertEqual(len(set(route['d'] for route in result['routes'])),6)
        for route in result['routes']:
            self.assertTrue(route['d'].startswith('M 100 80 C '))
            self.assertTrue(route['d'].endswith(', 500 80'))
            values=[float(value) for value in route['d'].replace('M','').replace('C','').replace(',','').split()]
            self.assertTrue(all(4 <= v <= 156 for v in values[3::2]))
        self.assertEqual(result['repeat'],result['routes'][2])
        self.assertTrue(result['resized']['d'].startswith('M 200 80 C '))
        self.assertTrue(result['resized']['d'].endswith(', 1000 80'))

    def test_drawing_retains_time_mapping_and_explains_visual_ownership(self):
        draw=helper('drawDependencies')
        for fragment in ['storyAxisFraction(sm)*sr.width','storyAxisFraction(tm)*tr.width','sr.height/2','tr.height/2','dataset.sourceLaneId','dataset.targetLaneId','dataset.routingBasis']:
            self.assertIn(fragment,draw)
        self.assertIn('not delegation or execution causality',helper('inspectCorrelation'))


if __name__=='__main__':
    unittest.main()
