"""Synthetic manual handoff controls; no provider calls or output execution."""
import json
from pathlib import Path
import subprocess
import unittest
ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'web/flow.js').read_text()

class OfflineExplanationControlsTests(unittest.TestCase):
    def test_modes_and_manual_workflow(self):
        html=(ROOT/'web/flow.html').read_text()
        self.assertIn('<option value="offline">Offline / Codex session</option><option value="api">API</option>',html)
        for text in ['does not connect to Codex automatically','No API key or provider parameters','Import Codex response','explanation-serve /path/to/recording.traceforge.json']:
            self.assertIn(text,html)
        mode=SOURCE[SOURCE.index("$('explanation-mode').onchange"):SOURCE.index('async function explanationOfflinePoll')]
        self.assertIn("$('explanation-key').value='';",mode)
        self.assertNotIn('explanationRequest(',mode)
        self.assertIn('explanationOfflinePoll()',mode)

    def test_queue_primary_and_optional_details(self):
        html=(ROOT/'web/flow.html').read_text()
        self.assertIn('id="explanation-offline-start"',html)
        self.assertIn('value="https://api.openai.com/v1"',html)
        self.assertIn('<details id="explanation-options">',html)
        self.assertNotIn('<details id="explanation-options" open',html)
        handler=SOURCE[SOURCE.index("$('explanation-offline-start').onclick"):SOURCE.index("$('explanation-offline-cancel').onclick")]
        self.assertIn("explanationRequest('/api/offline-start',body)",handler)
        self.assertNotIn('downloadExplanation(',handler)
        self.assertNotIn('/api/generate',handler)
        poll=SOURCE[SOURCE.index('async function explanationOfflinePoll'):SOURCE.index("$('explanation-offline-start').onclick")]
        for text in ['Waiting for Codex session. Action needed: send “Process pending descriptions in AgentStoryline” in your Codex chat. This queue does not notify or start Codex automatically. If you already asked, your session will save the results here. No file path is needed.',"explanationRequest('/api/offline-status')",'epoch!==explanationPollEpoch','explanationOfflinePollInFlight===epoch','await explanationAnnotations(epoch)','await explanationHistory(epoch)']:
            self.assertIn(text,poll)
        self.assertNotIn('setExplanationBusy(true)',poll)
        self.assertNotIn('reset(',poll)

    def test_queue_poll_wait_complete_and_stale_guard(self):
        functions=SOURCE[SOURCE.index('async function explanationOfflinePoll'):SOURCE.index("$('explanation-offline-start').onclick")]
        setup="""
let explanationPollEpoch=1,explanationDisposed=false,explanationOfflineBusy=false,explanationBusy=false,explanationOfflineWaiting=false,explanationOfflinePollInFlight=null,explanationTimer=0,annotations=0,history=0,resolveOld;
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);};
const explanationOffline=()=>true,setExplanationBusy=()=>{},checkExplanationBinding=()=>{},updateExplanationEstimate=()=>{};
const explanationAnnotations=async()=>{annotations++;},explanationHistory=async()=>{history++;};
let state={state:'waiting',total:2,completed:0};let explanationRequest=async()=>state;
"""
        scenario="""
(async()=>{await explanationOfflinePoll();clearTimeout(explanationTimer);const waiting=explanationOfflineWaiting&&$('explanation-status').textContent.startsWith('Waiting for Codex session.');state={state:'complete',total:2,completed:2};await explanationOfflinePoll();const complete=!explanationOfflineWaiting&&annotations===1&&history===1;state={state:'waiting',total:2,completed:0};$('explanation-status').textContent='Import failed: wrong recording';await explanationOfflinePoll(1,false);clearTimeout(explanationTimer);const quiet=$('explanation-status').textContent==='Import failed: wrong recording';explanationOfflineWaiting=false;explanationRequest=()=>new Promise(resolve=>{resolveOld=resolve;});const old=explanationOfflinePoll();explanationPollEpoch++;resolveOld({state:'waiting'});await old;process.stdout.write(JSON.stringify({waiting,complete,quiet,staleIgnored:!explanationOfflineWaiting}));})();
"""
        result=json.loads(subprocess.check_output(['node','-e',setup+functions+scenario],text=True))
        self.assertEqual(result,dict(waiting=True,complete=True,quiet=True,staleIgnored=True))

    def test_manual_actions_resume_quiet_queue_poll_and_api_wait_guidance(self):
        export=SOURCE[SOURCE.index("$('explanation-offline-export').onclick"):SOURCE.index("$('explanation-offline-import').onclick")]
        imported=SOURCE[SOURCE.index("$('explanation-response-file').onchange"):SOURCE.index("$('explanation-generate').onclick")]
        for handler in [export,imported]:
            self.assertIn('finishOfflineOperation(epoch);explanationOfflinePoll(epoch,false)',handler)
        self.assertIn('Switch to Offline / Codex session to cancel this request.',SOURCE)
        self.assertIn("$('explanation-retry').disabled=explanationOfflineWaiting||value",SOURCE)
        self.assertIn("$('explanation-retry').onclick=async()=>{if(explanationOfflineWaiting)return;",SOURCE)

    def test_provider_destination_and_empty_missing_feedback(self):
        html=(ROOT/'web/flow.html').read_text()
        self.assertIn('id="explanation-provider"',html)
        self.assertIn('Provider: OpenAI · change endpoint in Optional details',html)
        self.assertIn("new URL($('explanation-endpoint').value.trim()).origin",SOURCE)
        self.assertIn('No missing descriptions to improve. Use Optional details to regenerate.',SOURCE)
        for identifier in ['explanation-offline-export','explanation-offline-import']:
            self.assertIn("$('"+identifier+"').disabled=!explanationOffline()||",SOURCE)

    def test_raw_export_preserves_large_evidence_numbers(self):
        export=SOURCE[SOURCE.index("$('explanation-offline-export').onclick"):SOURCE.index("$('explanation-offline-import').onclick")]
        self.assertIn("raw=await response.text()",export)
        self.assertIn('downloadExplanation(raw,',export)
        self.assertNotIn('JSON.stringify(data)',export)
        self.assertIn("'X-TraceForge-Token':explanationService.token",export)
        self.assertIn('recording_id:explanationService.recording_id',export)
        self.assertIn('epoch!==explanationPollEpoch',export)

    def test_import_is_bounded_bound_and_preserves_playback(self):
        handler=SOURCE[SOURCE.index("$('explanation-response-file').onchange"):SOURCE.index("$('explanation-generate').onclick")]
        for text in ['16*1024*1024','response_json:raw','checkExplanationBinding(data)','await explanationAnnotations(epoch)','await explanationHistory(epoch)','event.target.value=\'\'']:
            self.assertIn(text,handler)
        for forbidden in ['reset(', 'eval(', 'explanationPoll(', '/api/generate']:
            self.assertNotIn(forbidden,handler)

    def test_offline_operation_invalidates_old_poll(self):
        functions=SOURCE[SOURCE.index('function startOfflineOperation'):SOURCE.index("$('explanation-offline-export').onclick")]
        setup="""
let explanationBusy=false,explanationOfflineBusy=false,explanationService={},explanationDisposed=false,explanationPollEpoch=2,explanationTimer=0;
const explanationOffline=()=>true,setExplanationBusy=v=>{explanationBusy=v;};
"""
        scenario="""
const epoch=startOfflineOperation();const started=epoch===3&&explanationBusy&&explanationOfflineBusy;
let blocked=false;try{startOfflineOperation();}catch{blocked=true;}
finishOfflineOperation(2);const staleIgnored=explanationBusy;finishOfflineOperation(epoch);
process.stdout.write(JSON.stringify({started,blocked,staleIgnored,finished:!explanationBusy&&!explanationOfflineBusy}));
"""
        result=json.loads(subprocess.check_output(['node','-e',setup+functions+scenario],text=True))
        self.assertEqual(result,dict(started=True,blocked=True,staleIgnored=True,finished=True))

if __name__=='__main__':unittest.main()
