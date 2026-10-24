"""New synthetic UI regressions for optional combined-session review."""
import json
from pathlib import Path
import subprocess
import unittest

SOURCE=(Path(__file__).resolve().parents[1]/'web/flow.js').read_text()
def helper(name):
    start=SOURCE.index('function '+name+'(')
    return SOURCE[start:SOURCE.index('\nfunction ',start+1)]
def node(script):
    return json.loads(subprocess.check_output(['node','-e',script],text=True))

class OrcaCollectionUITests(unittest.TestCase):
    def test_handoff_projection_is_strictly_orca_collection_only(self):
        declared=SOURCE[SOURCE.index('function declaredAdapter('):SOURCE.index('function sourceIdentity(')]
        projection=helper('orcaCollectionCorrelations')
        item={'id':'c1','kind':'tool_context_reuse','provenance':'content_inferred','source_event_id':'a','target_event_id':'b','source_session_id':'one','target_session_id':'two'}
        def payload(adapter, session_count=2):
            return {'bundle':{'manifest':{'source':{'adapter':adapter,'collection':{'sessions':[{'id':str(i)} for i in range(session_count)]}}}},'storyline':{'correlations':{'items':[item]}}}
        cases=[payload('orcareplay'),payload('orcareplay',1),payload('artex'),payload('codex'),payload('otlp'),payload('unknown')]
        script=declared+'\n'+projection+'\nconst cases='+json.dumps(cases)+';process.stdout.write(JSON.stringify(cases.map(orcaCollectionCorrelations).map(items=>items.length)));'
        self.assertEqual(node(script),[1,0,0,0,0,0])

    def test_handoff_ui_does_not_change_other_adapter_surfaces(self):
        css=(Path(__file__).resolve().parents[1]/'web/flow.css').read_text()
        self.assertIn("root.classList.toggle('f-orca-collection',isOrcaCollection)",SOURCE)
        self.assertIn("if(!isOrcaCollection)return;const strip=",SOURCE)
        self.assertIn("if(!isOrcaCollection||layout!=='single')return list",SOURCE)
        self.assertIn("if(e.kind==='content_correlation'&&!isOrcaCollection)continue",SOURCE)
        self.assertIn("isOrcaCollection?'Evidence links · recorded dependencies and content correlations':'Dependency links · select an activity to highlight its relationships'",SOURCE)
        self.assertNotIn('\n#tf-flow .f-handoff-',css)
        self.assertIn('#tf-flow.f-orca-collection .f-handoff-strip',css)

    def test_session_filter_uses_evidence_and_group_members_without_mutation(self):
        script=helper('sourceSessionId')+'\n'+helper('sessionMatches')+'''
const events=new Map([['e1',{data:{source_session:{id:'one'}}}],['e2',{data:{source_session:{id:'two'}}}]]),activities=[{id:'a1',event_ids:['e1']},{id:'a2',event_ids:['e2']}],items=[{source_session_id:'one'},{event_ids:['e2']},{source_activity_ids:['a1']},{activity:activities[1]}],before=JSON.stringify({items,activities});
process.stdout.write(JSON.stringify({all:items.map(i=>sessionMatches(i,'',events,activities)),one:items.map(i=>sessionMatches(i,'one',events,activities)),two:items.map(i=>sessionMatches(i,'two',events,activities)),unchanged:before===JSON.stringify({items,activities})}));'''
        result=node(script)
        self.assertEqual(result,{'all':[True]*4,'one':[True,False,True,False],'two':[False,True,False,True],'unchanged':True})

    def test_import_bounds_and_format_selection(self):
        script=helper('nativeImportFiles')+'''
const result=[];function check(files,kind){try{result.push(nativeImportFiles(files,kind).length)}catch{result.push('rejected')}}
check([{name:'one.orca',size:1}], 'orcareplay');check([{name:'one.orca',size:1},{name:'two.tar',size:1}], 'orcareplay');check([{name:'one.orca',size:1},{name:'two.tar',size:1}], 'artex');check(Array.from({length:17},()=>({name:'x.orca',size:1})),'orcareplay');check([{name:'x.orca',size:32*1024*1024},{name:'y.orca',size:1}],'orcareplay');check([{name:'x.orca',size:1},{name:'y.json',size:1}],'orcareplay');process.stdout.write(JSON.stringify(result));'''
        self.assertEqual(node(script),[1,2,'rejected','rejected','rejected','rejected'])

    def test_filter_is_presentation_only_and_import_defaults_to_all(self):
        self.assertIn("sessionFilter=''",SOURCE)
        self.assertIn("select.onchange=()=>{sessionFilter=select.value;buildStory();buildPanels();render();}",SOURCE)
        self.assertIn('visible.has(m.id)&&matchesSession(m)',SOURCE)
        self.assertIn('renderMilestones(shown)',SOURCE)
        self.assertIn('lane.activities.filter(matchesSession)',SOURCE)
        self.assertIn("input.multiple=button.dataset.importKind==='orcareplay'",SOURCE)
        self.assertIn("uploads.length>1?{files:uploads}:uploads[0]",SOURCE)
        self.assertIn("section('Source session',e.data.source_session)",SOURCE)
