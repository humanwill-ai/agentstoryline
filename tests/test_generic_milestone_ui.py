"""New synthetic browser-helper regressions; no model calls or real evidence."""
import json
from pathlib import Path
import subprocess
import unittest

SOURCE = (Path(__file__).resolve().parents[1] / 'web/flow.js').read_text()

def helper(name):
    start = SOURCE.index('function ' + name + '(')
    end = SOURCE.find('\nfunction ', start + 1)
    return SOURCE[start:end]

def node(script):
    return json.loads(subprocess.check_output(['node', '-e', script], text=True))

class GenericMilestoneUITests(unittest.TestCase):
    def test_nonsecurity_outcomes_reveal_and_mode_provenance(self):
        message = {'id':'m','actor_id':'worker','kind':'model.response','event_ids':['e'], 'timestamp':'2026-01-01T00:00:00Z','milestones':[{'label':'Report export failed','outcome':'failure','event_ids':['e'],'provenance':'source_reported','basis':'explicit_source_outcome'}]}
        script = helper('milestoneEntries') + '\nconst messages=' + json.dumps([message]) + ";const actionPairs=new Map(),annotations=new Map([['m',{milestones:[{label:'Draft awaits review',outcome:'unconfirmed',event_ids:['e']}]}]]);let textMode='original';const entityActor=m=>({id:m.actor_id}),ownerActor=entityActor;const original=milestoneEntries(messages),hidden=milestoneEntries([]);textMode='explained';const explained=milestoneEntries(messages);process.stdout.write(JSON.stringify({original,hidden,explained}));"
        data = node(script)
        self.assertEqual(data['hidden'], [])
        self.assertEqual([e['tag']['kind'] for e in data['original']], ['failure'])
        self.assertEqual([e['tag']['kind'] for e in data['explained']], ['failure','unconfirmed'])
        self.assertEqual(data['explained'][1]['tag']['provenance'], 'ai_inferred')
        self.assertEqual(data['explained'][1]['message']['timestamp'], message['timestamp'])
        self.assertEqual(data['explained'][1]['actor']['id'], 'worker')

    def test_import_validation_rejects_fabricated_or_unlinked_source_marker(self):
        payload = {'flow':{'messages':[{'id':'m','event_ids':['e'],'milestones':[{'label':'Report exported','outcome':'success','event_ids':['e'],'provenance':'source_reported','basis':'explicit_source_outcome'}]}]},'bundle':{'events':[{'id':'e','sequence':1}]}}
        script = helper('validateExplanations') + '\nconst p=' + json.dumps(payload) + ";const results=[];function check(){try{validateExplanations(p);results.push(true)}catch{results.push(false)}}check();p.flow.messages[0].milestones[0].event_ids=['missing'];check();p.flow.messages[0].milestones[0].event_ids=['e'];p.flow.messages[0].milestones[0].provenance='observed';check();process.stdout.write(JSON.stringify(results));"
        self.assertEqual(node(script), [True,False,False])

    def test_milestone_selection_defaults_on_and_is_sent_to_api_and_offline(self):
        self.assertIn("include_milestones:$('explanation-milestones').checked", SOURCE)
        self.assertIn('include_milestones:selected.include_milestones', SOURCE)
        html=(Path(__file__).resolve().parents[1]/'web/flow.html').read_text()
        self.assertIn('id="explanation-milestones" type="checkbox"',html)
        self.assertIn('id="explanation-milestones" type="checkbox" checked',html)

    def test_ai_profiles_legacy_and_evidence_constraints(self):
        annotation={'message_id':'m','text':'Summarizes the report','basis':'observed_action','event_ids':['e'], 'provenance':{'method':'offline-development-worker','model':'synthetic','provider':'offline','created_at':'2026-01-01T00:00:00Z','adapter_id':'generic','profile_version':1}}
        payload={'flow':{'messages':[{'id':'m','event_ids':['e']}]},'bundle':{'manifest':{'run_id':'run'},'events':[{'id':'e','sequence':1}]},'explanations':{'version':2,'run_id':'run','bundle_sha256':'a'*64,'profile':{'adapter_id':'generic','version':1},'provenance':{'method':'offline-development-worker','model':'synthetic','provider':'offline'},'annotations':[annotation]}}
        script=helper('validateExplanations')+'\nconst declaredAdapter=()=>"generic",p='+json.dumps(payload)+";const results=[];function check(){try{validateExplanations(p);results.push(true)}catch{results.push(false)}}check();const a=p.explanations.annotations[0];a.milestones=[{label:'Report awaits approval',outcome:'unconfirmed',event_ids:['e']}];check();p.explanations.profile.version=2;check();a.provenance.profile_version=2;check();a.milestones[0].event_ids=[];check();a.milestones[0].event_ids=['e'];a.milestones[0].outcome='probably';check();process.stdout.write(JSON.stringify(results));"
        self.assertEqual(node(script),[True,False,False,True,False,False])

    def test_source_milestone_rejects_additional_existing_message_event(self):
        payload={'flow':{'messages':[{'id':'m','event_ids':['e','prior'],'milestones':[{'label':'Report exported','outcome':'success','event_ids':['e','prior'],'provenance':'source_reported','basis':'explicit_source_outcome'}]}]},'bundle':{'events':[{'id':'prior','sequence':0},{'id':'e','sequence':1}]}}
        script=helper('validateExplanations')+'\nconst p='+json.dumps(payload)+";let accepted;try{validateExplanations(p);accepted=true}catch{accepted=false}process.stdout.write(JSON.stringify(accepted));"
        self.assertFalse(node(script))
