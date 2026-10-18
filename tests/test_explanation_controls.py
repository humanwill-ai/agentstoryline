"""Synthetic browser helper checks; no provider calls or private recording data."""
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'web/flow.js').read_text()
HELPERS = SOURCE[SOURCE.index('function validateExplanations('):SOURCE.index('function initialize(')]

def evaluate(expression, **values):
    script = HELPERS + '\n' + '\n'.join('const '+k+'='+json.dumps(v)+';' for k,v in values.items())
    return json.loads(subprocess.check_output(['node','-e',script+'\nprocess.stdout.write(JSON.stringify('+expression+'));'],text=True))

class ExplanationControlsTests(unittest.TestCase):
    def test_only_http_loopback_can_use_service(self):
        for protocol, host, expected in [('http:','127.0.0.1',True),('http:','localhost',True),('http:','[::1]',True),('file:','',False),('https:','localhost',False),('http:','example.com',False),('http:','localhost.evil',False)]:
            self.assertEqual(evaluate('explanationServiceOrigin(location)',location={'protocol':protocol,'hostname':host}), expected)

    def test_scope_estimate_missing_interval_all(self):
        rows=[{'id':'a','offset_ms':1000,'event_ids':['e']},{'id':'b','offset_ms':2000,'event_ids':['e'],'description':{'text':'recorded'}},{'id':'c','offset_ms':3000,'event_ids':['e']},{'id':'d','offset_ms':3000,'event_ids':['e'],'bookkeeping':True}]
        self.assertEqual(evaluate("['missing','interval','all'].map(scope=>explanationTargets(rows,new Map([['c',{}]]),scope,1500,2500).map(m=>m.id))",rows=rows),[['a'],['b'],['a','b','c']])

    def test_timestamp_input(self):
        self.assertEqual(evaluate("['0','12.5','01:02:03','bad','-1','1:2:3:4'].map(x=>explanationTimestamp(x))"),[0,12500,3723000,None,None,None])

    def test_v2_provenance_and_legacy_unknowns(self):
        provenance={'method':'provider-api','model':'synthetic','provider':'synthetic','created_at':'2026-10-15T12:00:00Z','adapter_id':'generic','profile_version':1}
        payload={'bundle':{'manifest':{'run_id':'synthetic','agent':{'name':'Synthetic'}},'events':[{'id':'e','sequence':1}]},'flow':{'messages':[{'id':'m','event_ids':['e']}]},'explanations':{'version':2,'run_id':'synthetic','bundle_sha256':'a'*64,'profile':{'adapter_id':'generic','version':1},'provenance':{k:provenance[k] for k in ('method','model','provider')},'annotations':[{'message_id':'m','text':'Synthetic action.','basis':'observed_action','event_ids':['e'],'provenance':provenance}]}}
        check="(()=>{try{validateExplanations(p);return true;}catch{return false;}})()"
        self.assertTrue(evaluate(check,p=payload))
        provenance.update(created_at=None,profile_version=0)
        self.assertTrue(evaluate(check,p=payload))
        for key,value in [('created_at','2026-10-15T12:00:00Z'),('profile_version',1),('adapter_id','artex'),('method','arbitrary')]:
            original=provenance[key];provenance[key]=value
            self.assertFalse(evaluate(check,p=payload))
            provenance[key]=original

    def test_credentials_and_recording_transport(self):
        self.assertNotIn('localStorage',SOURCE)
        self.assertNotIn('sessionStorage',SOURCE)
        self.assertIn("'X-TraceForge-Token':explanationService.token",SOURCE)
        self.assertIn('JSON.stringify({...body,recording_id:explanationService.recording_id})',SOURCE)
        self.assertIn('{recording_json:rawFileText}',SOURCE)
        self.assertIn("return response.text()",SOURCE)
        self.assertIn("$('explanation-key').value='';",SOURCE)
        self.assertIn("clone.querySelector('#tf-flow').replaceWith(template.cloneNode(true))",SOURCE)
        self.assertIn("raw.replace(/</g,'\\\\u003c')",SOURCE)

    def test_annotation_replacement_preserves_playback(self):
        replacement=SOURCE[SOURCE.index('function applyExplanationPackage('):SOURCE.index('function setExplanationBusy(')]
        self.assertIn('validatePackage(value)',replacement)
        self.assertIn('annotations.clear()',replacement)
        self.assertIn('render(false)',replacement)
        self.assertIn('inspectorRefresh=()=>inspectAction(entry)',SOURCE)
        for forbidden in ['seek(', 'reset(', 'position=', 'visible.clear(', 'playing=']:
            self.assertNotIn(forbidden,replacement)
        self.assertIn('provenance:explanation.provenance||p.explanations.provenance',SOURCE)

    def test_initial_probe_preserves_playback_and_import_syncs(self):
        probe=SOURCE[SOURCE.index("if(explanationServiceOrigin(window.location))fetch('/api/session'"):]
        self.assertNotIn('reset(',probe)
        self.assertNotIn("'/api/recording'",probe)
        self.assertIn('bindExplanationService()',probe)
        self.assertIn("const synchronized=validatePackage(await explanationRequest('/api/recording'))",SOURCE)
        self.assertIn("explanationRequest('/api/attachments',{attachments:next})",SOURCE)
        self.assertIn("clone.querySelector('#explanation-panel').replaceWith(explanationTemplate.cloneNode(true))",SOURCE)

    def test_old_complete_poll_cannot_overwrite_new_job(self):
        functions=SOURCE[SOURCE.index('function startExplanationJob('):SOURCE.index('function checkExplanationBinding(')]
        setup="""
let explanationPollEpoch=0,explanationStarting=false,explanationTimer=0,explanationCompleted=-1,explanationCanRetry=false,explanationDisposed=false,explanationPollInFlight=null,busy=false,resolveOld;
const elements=new Map();const $=id=>{if(!elements.has(id))elements.set(id,{textContent:'',value:0,open:false});return elements.get(id);};
const setExplanationBusy=value=>{busy=value;},checkExplanationBinding=()=>{},explanationAnnotations=async()=>{},explanationHistory=async()=>{},updateExplanationEstimate=()=>{};
let calls=0;const explanationRequest=()=>++calls===1?new Promise(resolve=>{resolveOld=resolve;}):Promise.resolve({state:'running',completed:0,total:2});
"""
        scenario="""
(async()=>{const old=explanationPoll();const epoch=startExplanationJob();resolveOld({state:'complete',completed:2,total:2});await old;const preserved=busy&&$('explanation-status').textContent==='Starting…';await explanationPoll(epoch);const blocked=calls===1;explanationStarting=false;await explanationPoll(epoch);clearTimeout(explanationTimer);process.stdout.write(JSON.stringify({preserved,blocked,current:busy&&$('explanation-status').textContent.startsWith('running')}));})();
"""
        result=json.loads(subprocess.check_output(['node','-e',setup+functions+scenario],text=True))
        self.assertEqual(result,{'preserved':True,'blocked':True,'current':True})

    def test_dialog_theme_and_retry_state(self):
        html=(ROOT/'web/flow.html').read_text()
        css=(ROOT/'web/flow.css').read_text()
        self.assertIn('body:has(#tf-flow.f-spotlight) .f-explanation-panel',css)
        self.assertIn('OpenAI-compatible · base URL ending /v1',html)
        self.assertIn('Blank uses service environment key, if configured',html)
        self.assertIn('Elapsed recorded time · from',html)
        self.assertIn("explanationCanRetry=['failed','cancelled'].includes(state.state)",SOURCE)
        self.assertIn("data.profile.adapter_id+' profile v'+data.profile.version",SOURCE)
        modal=SOURCE[SOURCE.index("$('improve-descriptions').onclick"):SOURCE.index("$('explanation-close').onclick")]
        self.assertIn('explanationOfflinePoll()',modal)
        self.assertIn('explanationHistory()',modal)

    def test_service_preview_and_binding_guards(self):
        preview=SOURCE[SOURCE.index('function updateExplanationEstimate('):SOURCE.index("$('improve-descriptions').onclick")]
        self.assertIn("explanationRequest('/api/preview',body)",preview)
        self.assertIn('250)',preview)
        self.assertIn('checkExplanationBinding(data)',preview)
        self.assertNotIn('api_key',preview)
        self.assertNotIn('explanationTargets(',preview)
        self.assertIn('explanationPreviewPending||!explanationPreview?.targets',SOURCE)
        self.assertIn('checkExplanationBinding(state)',SOURCE)
        self.assertIn('explanationService.recording_id=registered.recording_id',SOURCE)

    def test_explicit_action_and_offline_help(self):
        html=(ROOT/'web/flow.html').read_text()
        self.assertIn('type="password"',html)
        self.assertIn('Missing descriptions only',html)
        self.assertIn('explanation-serve /path/to/recording.traceforge.json',html)
        self.assertIn('Export a request, process it in this Codex session, then import its response.',html)
        self.assertIn('No provider calls occur until Generate descriptions is clicked.',SOURCE)
        self.assertIn("connect-src 'none'",html)
        self.assertEqual(SOURCE.count("explanationRequest('/api/generate'"),1)
        self.assertIn("$('explanation-generate').onclick=async",SOURCE)

if __name__=='__main__':unittest.main()
