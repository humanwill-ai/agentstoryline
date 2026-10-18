"""New synthetic artifact UI regressions; no runtime execution or model calls."""
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT/'web/flow.js').read_text()
HELPERS = SOURCE[SOURCE.index('function artifactIsShared('):SOURCE.index('function inspectArchiveFile(')]
HARNESS = r'''
let p={bundle:{manifest:{source:{}}}},current=p,explanationDisposed=false;
let explanationService={recording_id:'one',token:'session'},calls=[],pending=null;
const window={location:{}},explanationServiceOrigin=()=>true;
const document={createElement:()=>({click(){},set href(v){},set download(v){}})};
const n=(tag,text)=>({tag,textContent:text||'',children:[],isConnected:true,append(...items){this.children.push(...items)},setAttribute(){}});
const btn=(label,action)=>({...n('button',label),action,disabled:false});
const response=(data)=>({ok:true,json:async()=>data});
let fetch=async(path)=>{calls.push(path);return response({recording_id:'one',artifacts:[{path:'files/a.html',artifact_id:'opaque',available:true,bytes:20,sha256:'abc'}]});};
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
'''

def evaluate(code):
    script=HARNESS+HELPERS+'\n(async()=>{'+code+'})().catch(e=>{console.error(e);process.exit(1)});'
    return json.loads(subprocess.check_output(['node','-e',script],text=True))

class ArtifactFrontendTests(unittest.TestCase):
    def test_unavailable_and_shared_never_enable_actions_or_fetch_original(self):
        result=evaluate("p.sharing={kind:'sanitized-derivative'};const host=n('div');artifactControls(host,{path:'files/a.html'});await flush();process.stdout.write(JSON.stringify({calls,disabled:host.children[1].children.map(b=>b.disabled)}));")
        self.assertEqual(result,{'calls':[],'disabled':[True,True]})
        result=evaluate("explanationService=null;const host=n('div');artifactControls(host,{path:'files/a.html'});await flush();process.stdout.write(JSON.stringify({calls,disabled:host.children[1].children.map(b=>b.disabled),status:host.children[0].textContent}));")
        self.assertEqual(result['calls'],[])
        self.assertEqual(result['disabled'],[True,True])
        self.assertIn('standalone or portable',result['status'])

    def test_preview_literal_text_and_bounded_status(self):
        result=evaluate("const host=n('div');artifactControls(host,{path:'files/a.html'});await flush();fetch=async()=>response({recording_id:'one',text:'<script>alert(1)</script>',truncated:true});await host.children[1].children[0].action();process.stdout.write(JSON.stringify({text:host.children[2].textContent,status:host.children[0].textContent}));")
        self.assertEqual(result['text'],'<script>alert(1)</script>')
        self.assertIn('truncated',result['status'])
        controls=HELPERS[HELPERS.index('function artifactControls'):]
        self.assertNotIn('innerHTML',controls)
        self.assertNotIn('iframe',controls)

    def test_recording_change_rejects_inflight_response(self):
        result=evaluate("fetch=()=>new Promise(resolve=>pending=resolve);const request=artifactRequest('/api/artifact-preview','opaque').catch(e=>e.message);current={};pending(response({recording_id:'one',text:'stale'}));process.stdout.write(JSON.stringify(await request));")
        self.assertIn('Recording changed',result)

    def test_binary_preview_has_no_embedded_content(self):
        result=evaluate("const host=n('div');artifactControls(host,{path:'files/a.html'});await flush();fetch=async()=>response({recording_id:'one',binary:true,text:'ignored'});await host.children[1].children[0].action();process.stdout.write(JSON.stringify(host.children[2].textContent));")
        self.assertIn('Binary file',result)
        self.assertNotIn('ignored',result)

    def test_download_rejects_wrong_recording_header(self):
        result=evaluate("fetch=async()=>({ok:true,headers:{get:()=> 'other'},blob:async()=>{throw Error('must not read mismatched bytes')}});process.stdout.write(JSON.stringify(await artifactRequest('/api/artifact-download','opaque').catch(e=>e.message)));")
        self.assertIn('another recording',result)

    def test_controls_attach_before_availability_check(self):
        result=evaluate("const host=n('div');host.isConnected=false;artifactControls(host,{path:'files/a.html'});host.isConnected=true;await flush();process.stdout.write(JSON.stringify({calls,disabled:host.children[1].children.map(b=>b.disabled)}));")
        self.assertEqual(result,{'calls':['/api/artifacts'],'disabled':[False,False]})

    def test_backend_allowlist_controls_availability(self):
        result=evaluate("fetch=async()=>response({recording_id:'one',artifacts:[{path:'files/a.html',available:false,reason:'checksum mismatch'}]});const host=n('div');artifactControls(host,{path:'files/a.html',available:true});await flush();process.stdout.write(JSON.stringify({disabled:host.children[1].children.map(b=>b.disabled),status:host.children[0].textContent}));")
        self.assertEqual(result,{'disabled':[True,True],'status':'checksum mismatch'})

if __name__=='__main__':unittest.main()
