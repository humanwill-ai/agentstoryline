"""Synthetic OrcaReplay CLI, source-selection and local HTTP review checks."""
import base64
import copy
import http.client
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
import zipfile
from unittest.mock import Mock

from adapters.registry import source_identity
from analysis.explanation_profiles import profile_for
from analysis.explanation_service import ExplanationService, make_server
from analysis.explanation_workflow import load_portable
from tests.test_explanation_workflow import synthetic_bundle
from tests.test_viewer_import import FIXTURE as ARTEX, archive_bytes
from web.recording import write_recording
from web.render import build_payload
from web.viewer_import import detect_source, normalize_source

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'examples/experimental-orcareplay'
JS = (ROOT / 'web/flow.js').read_text()


def orca_tar(prefix=''):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        for path in sorted(EXAMPLE.rglob('*')):
            if path.is_file():
                raw = path.read_bytes()
                member = tarfile.TarInfo(prefix + path.relative_to(EXAMPLE).as_posix())
                member.size = len(raw)
                archive.addfile(member, io.BytesIO(raw))
    return output.getvalue()


def orca_zip(prefix='', manifest=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(EXAMPLE.rglob('*')):
            if path.is_file():
                raw = path.read_bytes()
                if path.name == 'manifest.json' and manifest is not None:
                    raw = json.dumps(manifest).encode()
                archive.writestr(prefix + path.relative_to(EXAMPLE).as_posix(), raw)
    return output.getvalue()


class OrcaReplayIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_content_dispatch_discriminates_containers_and_preserves_other_sources(self):
        self.assertEqual(detect_source(EXAMPLE), 'orcareplay')
        self.assertEqual(detect_source(ARTEX), 'artex')
        for name, raw, expected in [('renamed.json', orca_tar(), 'orcareplay'),
                                     ('nested.tar', orca_tar('run_abcd12/'), 'orcareplay'),
                                     ('native.orca', orca_zip(), 'orcareplay'),
                                     ('native.orca.zip', orca_zip('run_abcd12/'), 'orcareplay'),
                                     ('renamed.bin', orca_zip(), 'orcareplay'),
                                     ('unrelated.zip', orca_zip(manifest={'vendor': 'unrelated'}), 'unknown-zip'),
                                     ('artex.zip', orca_zip(manifest=json.loads((ARTEX / 'manifest.json').read_bytes())), 'unknown-zip'),
                                     ('artex.json', archive_bytes(), 'artex'),
                                     ('unrelated.tar', b'{"vendor":"unrelated"}', 'unknown-json')]:
            source = self.root / name
            source.write_bytes(raw)
            self.assertEqual(detect_source(source), expected)
            if expected in ('artex', 'orcareplay'):
                package, _ = normalize_source(source, self.root / 'state')
                self.assertEqual(package['payload']['bundle']['manifest']['source']['adapter'], expected)
        unknown = self.root / 'unknown'
        unknown.mkdir()
        (unknown / 'manifest.json').write_text('{"vendor":"unrelated"}')
        self.assertEqual(detect_source(unknown), 'unknown-json')
        with self.assertRaisesRegex(ValueError, 'unrecognized recording'):
            normalize_source(unknown, self.root / 'state')
        # A manifest alone cannot supply the missing recorded event stream.
        self.assertEqual(detect_source(EXAMPLE / 'manifest.json'), 'unknown-json')

    def test_zip_detection_rejects_malformed_and_arbitrary_archives(self):
        for raw in [b'PK\x03\x04truncated', b'PKnot a ZIP']:
            source = self.root / 'malformed.orca'
            source.write_bytes(raw)
            with self.assertRaises(ValueError):
                detect_source(source)
            with self.assertRaises(ValueError):
                normalize_source(source, self.root / 'state')
        source = self.root / 'unrelated.orca'
        source.write_bytes(orca_zip(manifest={'vendor': 'unrelated'}))
        with self.assertRaisesRegex(ValueError, 'unrecognized recording'):
            normalize_source(source, self.root / 'state')
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('notes.txt', 'An arbitrary ZIP is not an Orca recording.')
        source.write_bytes(output.getvalue())
        with self.assertRaises(ValueError):
            detect_source(source)

    def test_identity_generic_profile_cli_and_portable_export(self):
        original = {p.relative_to(EXAMPLE): p.read_bytes() for p in EXAMPLE.rglob('*') if p.is_file()}
        bundle = self.root / 'bundle'
        result = subprocess.run([sys.executable, str(ROOT / 'agentstoryline.py'), 'import-orcareplay',
                                 str(EXAMPLE), str(bundle)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        recording = self.root / 'recording.json'
        result = subprocess.run([sys.executable, str(ROOT / 'agentstoryline.py'), 'timeline', str(bundle),
                                 str(self.root / 'timeline.html'), '--recording-output', str(recording)],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        package = load_portable(recording)
        source = source_identity(package['payload']['bundle'])
        self.assertEqual((source['adapter_id'], source['support_tier']), ('orcareplay', 'experimental'))
        self.assertEqual(profile_for(package['payload']['bundle']).adapter_id, 'generic')
        spoofed = copy.deepcopy(package['payload']['bundle'])
        spoofed['manifest']['agent']['name'] = 'Artex'
        self.assertEqual(source_identity(spoofed)['adapter_id'], 'orcareplay')
        self.assertEqual(profile_for(spoofed).adapter_id, 'generic')
        self.assertEqual(original, {p.relative_to(EXAMPLE): p.read_bytes() for p in EXAMPLE.rglob('*') if p.is_file()})

    def test_cli_native_zip_import_and_export_matches_directory_source(self):
        expected, _ = normalize_source(EXAMPLE, self.root / 'expected-state')
        for filename in ['recording.orca', 'recording.orca.zip']:
            with self.subTest(filename=filename):
                source = self.root / filename
                original = orca_zip('run_abcd12/')
                source.write_bytes(original)
                bundle = self.root / (filename + '-bundle')
                imported = subprocess.run([sys.executable, str(ROOT / 'agentstoryline.py'),
                    'import-orcareplay', str(source), str(bundle)], cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(imported.returncode, 0, imported.stderr)
                recording = self.root / (filename + '-recording.json')
                rendered = subprocess.run([sys.executable, str(ROOT / 'agentstoryline.py'), 'timeline',
                    str(bundle), str(self.root / (filename + '.html')), '--recording-output', str(recording)],
                    cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(rendered.returncode, 0, rendered.stderr)
                self.assertEqual(load_portable(recording)['payload']['bundle'], expected['payload']['bundle'])
                self.assertEqual(source.read_bytes(), original)

    @unittest.skipUnless(shutil.which('node'), 'Node required for frontend checks')
    def test_source_menu_selects_native_zip_and_tar_without_directory_picker_and_badge(self):
        prepare = JS[JS.index('function prepareImportPicker('):JS.index('\nprepareImportPicker();')]
        setup = JS[JS.index('function setupImportPicker('):JS.index('\nfunction setupImport(){')]
        badge = JS[JS.index('function declaredAdapter('):JS.index('\nfunction initialize(')]
        # Exercise actual menu construction and selection using a tiny DOM surface.
        harness = r'''
const nodes=new Map();
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.attrs={};}
 set id(value){this._id=value;nodes.set(value,this);} get id(){return this._id;}
 setAttribute(k,v){this.attrs[k]=v;} append(...items){for(const item of items){item.parent=this;this.children.push(item);}}
 closest(selector){if(selector==='label')return label;if(selector==='.f-import')return container;if(selector==='.f-import-picker')return picker;}
 replaceWith(value){picker=value;} after(){} addEventListener(){} focus(){this.focused=true;} click(){this.clicked=true;}
 querySelectorAll(tag){return this.children.filter(x=>x.tag===tag);} querySelector(tag){return this.children.find(x=>x.tag===tag);}
 contains(){return false;}
}
let picker;const label=new Element('label'),container=new Element('section'),input=new Element('input');input.id='open-recording';
for(const id of ['tf-flow','import-status','source-badge','source-note']){const e=new Element('div');e.id=id;}
const document={getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag)};
const exportLoadedRecording=()=>{},current=null,explanationService=null;
'''
        assertions = r'''
prepareImportPicker();setupImportPicker();
const choice=nodes.get('import-source-choices').children.find(x=>x.dataset.importKind==='orcareplay');
if(!choice||!choice.querySelector('small').textContent.includes('experimental'))throw Error('missing experimental source');
choice.onclick();
if(!input.clicked||input.accept!=='.orca,.orca.zip,.zip,.tar,application/zip,application/x-tar'||input.dataset.importKind!=='orcareplay'||input.webkitdirectory)throw Error('incorrect source picker');
renderSourceBadge({bundle:{manifest:{source:{adapter:'orcareplay'},agent:{name:'Artex'}}}});
if(nodes.get('source-badge').textContent!=='OrcaReplay · experimental')throw Error('incorrect source badge');
if(!nodes.get('source-note').textContent.includes('not attested'))throw Error('missing provenance boundary');
console.log('ok');
'''
        result = subprocess.run(['node', '-e', harness + prepare + setup + badge + assertions],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for frontend checks')
    def test_native_zip_extensions_route_binary_files_without_json_decoding(self):
        setup = JS[JS.index('function setupImport(){'):JS.index('\nasync function exportLoadedRecording(')]
        harness = r'''
const nodes=new Map(['open-recording','open-script','import-status'].map(id=>[id,{value:'',disabled:false}]));
const document={getElementById:id=>nodes.get(id)};
let importInFlight=false,explanationBusy=false,explanationService=null,attachments=[],current={},nativeCalls=[];
const setupTelemetry=()=>{},setupShare=()=>{},setupImportPicker=()=>{},setImportChooserDisabled=()=>{},renderFiles=()=>{};
const openNativeRecording=async file=>{nativeCalls.push(file.name);};
const validatePackage=value=>value,reset=()=>{};
'''
        assertions = r'''
(async()=>{
 setupImport();
 for(const name of ['run.orca','run.ORCA','run.orca.zip','run.ORCA.ZIP','run.zip','run.tar','run.tar.zst','run.jsonl']){
  const before=nativeCalls.length,file={name,size:20,text:async()=>{throw Error('binary native file decoded as JSON');}};
  await nodes.get('open-recording').onchange({target:{files:[file]}});
  if(nativeCalls.length!==before+1||nativeCalls.at(-1)!==name)throw Error('native route failed: '+name+' '+nodes.get('import-status').textContent);
 }
 let textReads=0;
 const portable={name:'portable.json',size:20,text:async()=>{textReads++;return JSON.stringify({format:'traceforge-recording',payload:{}});}};
 const before=nativeCalls.length;
 await nodes.get('open-recording').onchange({target:{files:[portable]}});
 if(textReads!==1||nativeCalls.length!==before)throw Error('portable JSON stopped opening locally');
 const telemetry={name:'telemetry.json',size:20,text:async()=>JSON.stringify({resourceSpans:[]})};
 await nodes.get('open-recording').onchange({target:{files:[telemetry]}});
 if(nativeCalls.at(-1)!==telemetry.name)throw Error('native JSON stopped reaching service detection');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run(['node', '-e', harness + setup + assertions], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for frontend checks')
    def test_compact_card_status_distinguishes_tool_proposals_from_invocations(self):
        make_action = JS[JS.index('function makeAction('):JS.index('\nfunction makeMessage(')]
        harness = r'''
function n(tag,text,classes){return {tag,text,classes,children:[],dataset:{},classList:{toggle(){}},append(...children){this.children.push(...children);}};}
const btn=(text,onclick,classes)=>n('button',text,classes),ownerActor=()=>({}),avatar=()=>n('img'),selectedInEntry=()=>false,displayName=()=>'Synthetic agent',chatEntities=new Map(),textMode='original';
function appendMessageTags(){} function messageStamp(){} function mentionText(element,text){element.text=text;}
'''
        assertions = r'''
for(const [status,expected,state] of [['proposal_only_not_execution','Proposal recorded','proposal'],['source_observed_shell_invocation','Invocation recorded','invocation']]){
 const message={id:'m',kind:'tool.start',execution_status:status,action_label:'Recorded tool',text:'Recorded evidence'},row=makeAction({id:'entry',messages:[message]}),button=row.children[1];
 if(button.children.find(x=>x.classes==='f-action-state').text!==expected||button.dataset.state!==state)throw Error('incorrect tool status '+status);
}
const result=makeAction({id:'entry',messages:[{id:'r',kind:'tool.end',text:'Source reported result'}]}).children[1];
if(result.children.find(x=>x.classes==='f-action-state').text!=='Result recorded')throw Error('incorrect result status');
'''
        result = subprocess.run(['node', '-e', harness + make_action + assertions], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_http_upload_originals_descriptions_and_portable_roundtrip(self):
        initial = self.root / 'initial.json'
        write_recording(build_payload(synthetic_bundle(), {}), initial)
        provider = Mock(side_effect=AssertionError('review must not invoke provider'))
        service = ExplanationService(initial, self.root / 'state', provider=provider)
        server = make_server(service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        origin = 'http://127.0.0.1:' + str(server.server_port)

        def request(path, value=None, raw=False, status=200):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            headers = {'Content-Type': 'application/json', 'Origin': origin, 'X-TraceForge-Token': service.token}
            try:
                connection.request('POST' if value is not None else 'GET', path,
                                   None if value is None else json.dumps(value).encode(), headers)
                response = connection.getresponse()
                body = response.read()
                self.assertEqual(response.status, status, body[:500])
                return body if raw else json.loads(body)
            finally:
                connection.close()

        previous_bundle = None
        for filename, raw in [('synthetic.tar', orca_tar('run_abcd12/')),
                              ('synthetic.orca', orca_zip()),
                              ('synthetic.orca.zip', orca_zip('run_abcd12/')),
                              ('synthetic.zip', orca_zip())]:
            with self.subTest(filename=filename):
                imported = request('/api/import-native', {'recording_id': service.recording_id,
                    'filename': filename, 'source_base64': base64.b64encode(raw).decode()})
                self.assertEqual(imported['source']['adapter_id'], 'orcareplay')
                package = request('/api/recording')
                current_bundle = package['payload']['bundle']
                if previous_bundle is not None:
                    self.assertEqual(current_bundle, previous_bundle)
                previous_bundle = current_bundle
                rows = request('/api/artifacts', {'recording_id': service.recording_id})['artifacts']
                row = next(row for row in rows if row['path'] == 'files/events.jsonl')
                self.assertTrue(row['available'])
                self.assertEqual(request('/api/artifact-download', {'recording_id': service.recording_id,
                    'artifact_id': row['artifact_id']}, raw=True), (EXAMPLE / 'events.jsonl').read_bytes())
        active_id, active_package = service.recording_id, copy.deepcopy(service.package)
        for filename, raw in [('broken.orca', b'PK\x03\x04truncated'),
                              ('unrelated.orca.zip', orca_zip(manifest={'vendor': 'unrelated'})),
                              ('typo.ora', orca_zip())]:
            request('/api/import-native', {'recording_id': service.recording_id, 'filename': filename,
                'source_base64': base64.b64encode(raw).decode()}, status=400)
            self.assertEqual((service.recording_id, service.package), (active_id, active_package))
        # Status and new handoffs advertise the current enrichment profile.
        expected_profile = profile_for(service.bundle).identity
        self.assertEqual(request('/api/status')['profile'], expected_profile)
        package = request('/api/recording')
        self.assertEqual(package['payload']['flow']['time_mode'], 'recorded')
        self.assertTrue(package['payload']['storyline']['activities'])
        proposed = next(m for m in package['payload']['flow']['messages'] if m['kind'] == 'tool.start')
        self.assertTrue(proposed['title'].startswith('Tool proposal:'))
        self.assertIn('execution not established', proposed['text'])
        self.assertTrue(proposed['compact_text'].startswith('Proposed tool:'))
        activity = next(a for a in package['payload']['storyline']['activities'] if proposed['event_ids'][0] in a['event_ids'])
        self.assertTrue(activity['label'].startswith('Tool proposal:'))
        by_source_kind = {e['id']: e['data']['source_kind'] for e in package['payload']['bundle']['events']}
        observations = {by_source_kind[m['event_ids'][0]]: m for m in package['payload']['flow']['messages']}
        for source_kind, title in [('run.start', 'Run start'), ('run.end', 'Run end'),
                                   ('fs.snapshot', 'Filesystem snapshot metadata'), ('fs.change', 'Filesystem change')]:
            self.assertEqual(observations[source_kind]['title'], title)
            self.assertTrue(observations[source_kind]['compact_text'].startswith(title))
        self.assertIn('Synthetic snapshot metadata only', observations['fs.snapshot']['text'])
        self.assertIn('OrcaReplay', request('/', raw=True).decode())
        rows = request('/api/artifacts', {'recording_id': service.recording_id})['artifacts']
        row = next(row for row in rows if row['path'] == 'files/events.jsonl')
        self.assertTrue(row['available'])
        identity = {'recording_id': service.recording_id, 'artifact_id': row['artifact_id']}
        self.assertEqual(request('/api/artifact-download', identity, raw=True), (EXAMPLE / 'events.jsonl').read_bytes())
        self.assertIn('model.request', request('/api/artifact-preview', identity)['text'])
        handoff = request('/api/offline-request', {'recording_id': service.recording_id, 'scope': 'all', 'model': 'synthetic-worker'})
        self.assertEqual(handoff['profile'], expected_profile)
        documents = []
        for batch in handoff['requests']:
            document = copy.deepcopy(batch['response_format'])
            document['provenance']['provider'] = 'synthetic-worker'
            # Older profile-v1 annotations without milestones remain importable.
            document['annotations'] = [dict(message_id=t['message_id'], text='Reviews recorded synthetic evidence.',
                basis='observed_action', event_ids=[t['own_event_id']],
                provenance=dict(document['provenance'], created_at='2026-10-18T12:00:00Z', adapter_id='generic', profile_version=1))
                for t in batch['targets']]
            documents.append(document)
        response = {'format': 'traceforge-explanation-response-set', 'version': 1,
            'request_id': handoff['request_id'], 'bundle_sha256': handoff['bundle_sha256'], 'responses': documents}
        request('/api/offline-import', {'recording_id': service.recording_id, 'response_json': json.dumps(response)})
        exported = request('/api/export')
        self.assertTrue(exported['payload']['explanations']['annotations'])
        self.assertEqual(exported['payload']['bundle'], package['payload']['bundle'])
        saved = self.root / 'export.json'
        saved.write_text(json.dumps(exported))
        reopened = ExplanationService(saved, self.root / 'state', provider=provider)
        self.assertEqual(reopened.package['payload']['explanations'], exported['payload']['explanations'])
        # A portable copy cannot authorize original byte access in another state directory.
        detached = ExplanationService(saved, self.root / 'detached', provider=provider)
        self.assertFalse(any(r['available'] for r in detached.artifacts.listing()['artifacts']))
        provider.assert_not_called()
