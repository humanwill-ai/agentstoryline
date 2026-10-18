"""New synthetic tests for offline explanations; no run evidence or model calls."""
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

def payload():
    return {'bundle':{'manifest':{'run_id':'synthetic'},'events':[{'id':'e1','sequence':1},{'id':'e2','sequence':2}]},'flow':{'messages':[{'id':'call','event_ids':['e1'],'text':'literal call'},{'id':'result','event_ids':['e2'],'text':'literal result'}]},'explanations':{'version':1,'run_id':'synthetic','bundle_sha256':'a'*64,'provenance':{'method':'offline-development-worker','model':'synthetic-model','provider':'synthetic-provider'},'annotations':[{'message_id':'result','text':'<script>synthetic</script>','basis':'inferred_purpose','event_ids':['e2']}]}}

class ExplanationFrontendTests(unittest.TestCase):
    def test_mode_fallback_and_explicit_preference(self):
        self.assertEqual(evaluate("[explanationMode(undefined,[],new Map()),explanationMode(undefined,[{id:'m',description:{text:'recorded'}}],new Map()),explanationMode('original',[{id:'m'}],new Map([['m',{}]]))]"),['original','explained','original'])

    def test_latest_revealed_message_never_gets_future_result_annotation(self):
        p=payload()
        self.assertEqual(evaluate("(()=>{const annotations=new Map(p.explanations.annotations.map(a=>[a.message_id,a]));return p.flow.messages.map(m=>explanationFor(m,annotations));})()",p=p)[0],None)
        self.assertIn('explanationFor(entry.messages.at(-1),annotations)',SOURCE)

    def test_literal_text_and_original_message_invariance(self):
        p=payload()
        result=evaluate("(()=>{const before=JSON.stringify(p);validateExplanations(p);const explanation=explanationFor(p.flow.messages[1],new Map(p.explanations.annotations.map(a=>[a.message_id,a])));return {same:before===JSON.stringify(p),explanation};})()",p=p)
        self.assertTrue(result['same'])
        self.assertEqual(result['explanation']['text'],'<script>synthetic</script>')
        self.assertEqual(result['explanation']['label'],'AI interpretation')
        self.assertIn("mentionText(body,summary",SOURCE)
        self.assertIn("document.createTextNode(rest)",SOURCE)

    def test_annotation_then_recorded_description_then_original_fallback(self):
        result=evaluate("(()=>{const message={id:'m',text:'exact original',description:{text:'recorded purpose',basis:'recorded',event_ids:[],source_path:'arguments.description'}};return [explanationFor(message,new Map([['m',{text:'generated purpose',basis:'observed_action'}]])),explanationFor(message,new Map()),explanationFor({id:'raw',text:'exact original'},new Map())];})()")
        self.assertEqual([item['text'] if item else None for item in result],['generated purpose','recorded purpose',None])
        self.assertEqual(result[1]['label'],'Recorded description')

    def test_bookkeeping_visibility_only_explicit_true(self):
        rows=[{'id':'known','bookkeeping':True},{'id':'observation'},{'id':'string','bookkeeping':'true'}]
        self.assertEqual(evaluate("explanationChatMessages(rows,'original')",rows=rows),rows)
        self.assertEqual([m['id'] for m in evaluate("explanationChatMessages(rows,'explained')",rows=rows)],['observation','string'])
        self.assertIn('renderMilestones(shown)',SOURCE)

    def test_import_rejects_bad_binding_limits_identifiers_and_future_evidence(self):
        cases=[]
        for key,value in [('run_id','other'),('bundle_sha256','bad')]:
            p=payload();p['explanations'][key]=value;cases.append(p)
        for key,value in [('text','x'*281),('basis','guess'),('message_id','missing'),('event_ids',['missing']),('text','two\nlines'),('text','separator\u2028line'),('extra','unknown')]:
            p=payload();p['explanations']['annotations'][0][key]=value;cases.append(p)
        p=payload();p['explanations']['annotations'][0]['message_id']='call';cases.append(p)
        p=payload();p['explanations']['annotations'][0]['event_ids']=['e2','e1'];cases.append(p)
        p=payload();p['explanations']['annotations']=[];cases.append(p)
        p=payload();p['explanations']['provenance']['extra']='unknown';cases.append(p)
        for p in cases:
            self.assertTrue(evaluate("(()=>{try{validateExplanations(p);return false;}catch{return true;}})()",p=p))
        self.assertTrue(evaluate("(()=>{validateExplanations(p);return true;})()",p=payload()))

    def test_connected_recorded_prior_evidence_is_accepted(self):
        p=payload();p['bundle']['events'][1]['dependencies']=[{'event_id':'e1','provenance':'recorded'}];p['explanations']['annotations'][0]['event_ids']=['e2','e1']
        self.assertTrue(evaluate("(()=>{validateExplanations(p);return true;})()",p=p))

    def test_coverage_counts_disjoint_visible_fallbacks(self):
        rows=[{'id':'ai','description':{'text':'also recorded'}},{'id':'recorded','description':{'text':'recorded'}},{'id':'raw'},{'id':'hidden','bookkeeping':True,'description':{'text':'hidden'}}]
        self.assertEqual(evaluate("explanationCoverage(rows,new Map([['ai',{}],['hidden',{}]]))",rows=rows),{'ai':1,'recorded':1,'original':1,'bookkeeping':1})

    def test_switch_is_separate_and_preserves_playback(self):
        html=(ROOT/'web/flow.html').read_text()
        self.assertIn('aria-label="Message text"',html)
        self.assertIn('aria-label="Message detail"',html)
        switch=SOURCE[SOURCE.index("for(const b of root.querySelectorAll('[data-text-mode]'))"):SOURCE.index('let dependencySvg=')]
        self.assertNotIn('playing=',switch)
        self.assertNotIn('seek(',switch)
        self.assertIn('textMode:document.querySelector',SOURCE)

if __name__=='__main__':unittest.main()
