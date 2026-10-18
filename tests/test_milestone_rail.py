"""New synthetic milestone projection/render tests; no recorded run evidence."""
import json
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('TRACEFORGE_FLOW_JS', ROOT / 'web' / 'flow.js')).read_text()
HELPERS = SOURCE[SOURCE.index('function milestoneEntries('):SOURCE.index('function render(notify=')]
OPEN = SOURCE[SOURCE.index('function open(title)'):SOURCE.index('\n', SOURCE.index('function open(title)'))]
CLOSE = SOURCE[SOURCE.index("$('close').onclick="):SOURCE.index(";$('design').onchange", SOURCE.index("$('close').onclick="))]


def evaluate(expression, rows, shown=None, pairs=None, **variables):
    values = dict(messages=rows, shown=rows if shown is None else shown, pairs=pairs or [], **variables)
    script = '\n'.join('const ' + key + '=' + json.dumps(value) + ';' for key, value in values.items())
    script += '''
const actionPairs=new Map(pairs);
const ownerActor=m=>({id:m.owner_entity_id||m.entity_id||m.actor_id,avatar_kind:m.avatar_kind});
const entityActor=m=>({id:m.entity_id||m.actor_id});
const inspected=[];
const inspect=m=>inspected.push(m.id);
const displayName=a=>a?.id||'Recorded source';
const compactTimestamp=s=>String(s).match(/T(\\d{2}:\\d{2}:\\d{2})/)?.[1]||s;
function n(tag,text,cls){return {tag,text,className:cls,children:[],dataset:{},append(...x){this.children.push(...x);}};}
const avatar=a=>n('avatar',a?.id||'Recorded source');
const btn=(text,click,cls)=>Object.assign(n('button',text,cls),{click});
const host={dataset:{},children:[],scrollHeight:500,scrollTop:70,clientHeight:100,
 append(...x){for(const child of x){child.remove=()=>this.children.splice(this.children.indexOf(child),1);this.children.push(child);}},replaceChildren(){this.children=[];},get lastElementChild(){return this.children.at(-1);}};
function classes(){const values=new Set();return {add:x=>values.add(x),remove:x=>values.delete(x),contains:x=>values.has(x)};}
const root={classList:classes()},rail={inert:false,setAttribute(){},removeAttribute(){}},detail={classList:classes()},detailBody={replaceChildren(){}},close={};
const $=id=>({'milestone-list':host,milestones:rail,detail,'detail-body':detailBody,close}[id]||host);
const timers=[];const setTimeout=fn=>timers.push(fn);
'''
    script += HELPERS + '\n' + OPEN + '\n' + CLOSE + ';\nprocess.stdout.write(JSON.stringify(' + expression + '));'
    return json.loads(subprocess.check_output(['node', '-e', script], text=True))


def message(identifier, kind='tool.end', owner='worker:one', **fields):
    return {'id': identifier, 'kind': kind, 'actor_id': 'record:' + identifier,
            'entity_id': 'source:' + identifier, 'owner_entity_id': owner,
            'semantic_tags': [{'kind': 'success', 'label': 'Token obtained',
                               'basis': 'synthetic_observation', 'event_ids': ['event:' + identifier]}], **fields}


class MilestoneRailTests(unittest.TestCase):
    def test_only_visible_markers_are_projected(self):
        call = message('call', kind='tool.start', semantic_tags=[])
        result = message('result')
        self.assertEqual(evaluate('milestoneEntries(shown)', [call, result], shown=[call], pairs=[['result', 'call']]), [])
        revealed = evaluate('milestoneEntries(shown)', [call, result], pairs=[['result', 'call']])
        self.assertEqual([entry['message']['id'] for entry in revealed], ['result'])

    def test_tool_result_credits_structurally_paired_invoker_not_target(self):
        call = message('call', kind='tool.start', owner='worker:seven', semantic_tags=[])
        result = message('result', owner=None, target_entity_id='target:synthetic')
        entries = evaluate('milestoneEntries(shown)', [call, result], pairs=[['result', 'call']])
        self.assertEqual(entries[0]['actor']['id'], 'worker:seven')
        self.assertEqual(entries[0]['message'], result)

    def test_unpaired_result_stays_unattributed_without_guessing_adjacent_invoker(self):
        call = message('call', kind='tool.start', owner='worker:other', semantic_tags=[])
        result = message('result', owner=None, target_entity_id='target:synthetic')
        entries = evaluate('milestoneEntries(shown)', [call, result])
        self.assertIsNone(entries[0]['actor'])

    def test_recorded_worker_identity_remains_usable_without_owner_field(self):
        row = message('result', owner=None, entity_id='worker:recorded', avatar_kind='worker')
        self.assertEqual(evaluate('milestoneEntries(shown)', [row])[0]['actor']['id'], 'worker:recorded')

    def test_non_tool_marker_keeps_entity_identity(self):
        row = message('model', kind='model.response', owner='worker:one', entity_id='model:synthetic')
        self.assertEqual(evaluate('milestoneEntries(shown)', [row])[0]['actor']['id'], 'model:synthetic')

    def test_duplicate_source_marker_collapses_but_distinct_observations_survive(self):
        first = message('one')
        first['semantic_tags'].append(dict(first['semantic_tags'][0]))
        second = message('two', owner='worker:two')
        entries = evaluate('milestoneEntries(shown)', [first, second])
        self.assertEqual([entry['message']['id'] for entry in entries], ['one', 'two'])

    def test_shared_evidence_does_not_collapse_different_workers_observations(self):
        first = message('one')
        second = message('two', owner='worker:two', semantic_tags=first['semantic_tags'])
        entries = evaluate('milestoneEntries(shown)', [first, second])
        self.assertEqual([entry['actor']['id'] for entry in entries], ['worker:one', 'worker:two'])

    def test_unknown_marker_ignored_and_danger_preserved(self):
        row = message('danger', kind='tool.start', semantic_tags=[
            {'kind': 'neutral', 'label': 'Synthetic'},
            {'kind': 'danger', 'label': 'Exploitation attempt', 'basis': 'recorded_phase'}])
        entries = evaluate('milestoneEntries(shown)', [row])
        self.assertEqual([entry['tag']['kind'] for entry in entries], ['danger'])

    def test_projection_does_not_mutate_messages_or_marker_evidence(self):
        row = message('one', semantic_tags=[{'kind': 'success', 'label': 'Synthetic', 'basis': 'synthetic', 'event_ids': ['z', 'a', 'z']}])
        outcome = evaluate('(()=>{const before=JSON.stringify(messages);milestoneEntries(shown);return before===JSON.stringify(messages);})()', [row])
        self.assertTrue(outcome)

    def test_render_preserves_exact_timestamp_safe_text_and_inspection(self):
        row = message('one', timestamp='2026-10-12T12:34:56+02:00')
        row['semantic_tags'][0]['label'] = '<img src=x onerror=synthetic>'
        rendered = evaluate('(()=>{renderMilestones(shown);host.children[0].click();return {card:host.children[0],inspected};})()', [row])
        self.assertEqual(rendered['inspected'], ['one'])
        card = rendered['card']
        self.assertEqual(card['children'][1]['text'], row['semantic_tags'][0]['label'])
        self.assertEqual(card['children'][3]['dateTime'], row['timestamp'])
        self.assertEqual(card['children'][3]['text'], '12:34:56')

    def test_untimed_marker_has_no_invented_datetime(self):
        card = evaluate('(()=>{renderMilestones(shown);return host.children[0];})()', [message('untimed')])
        self.assertEqual(card['children'][3]['text'], 'Time unavailable')
        self.assertNotIn('dateTime', card['children'][3])

    def test_rebuild_retains_manual_scroll_and_removes_future_entries(self):
        rows = [message('one'), message('two')]
        rendered = evaluate('(()=>{renderMilestones(messages);renderMilestones(messages.slice(0,1));return {ids:host.children.map(c=>c.dataset.messageId),top:host.scrollTop};})()', rows)
        self.assertEqual(rendered, {'ids': ['one'], 'top': 70})

    def test_restart_empty_projection_replaces_cards(self):
        result = evaluate('(()=>{renderMilestones(messages);renderMilestones([]);return host.children;})()', [message('one')])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['className'], 'f-milestone-empty')

    def test_following_rail_scrolls_to_latest(self):
        result = evaluate('(()=>{host.scrollTop=400;renderMilestones(messages);return host.scrollTop;})()', [message('one')])
        self.assertEqual(result, 500)

    def test_new_arrival_retains_earlier_card_identity_and_backseek_removes_suffix(self):
        result = evaluate('(()=>{renderMilestones(messages.slice(0,1));const first=host.children[0];first.animationProgress="retained";renderMilestones(messages);const appended=host.children[0]===first;renderMilestones(messages.slice(0,1));return {appended,retained:host.children[0]===first,animation:host.children[0].animationProgress,count:host.children.length};})()', [message('one'), message('two')])
        self.assertEqual(result, {'appended': True, 'retained': True, 'animation': 'retained', 'count': 1})

    def test_detail_keeps_rail_active_and_manual_scroll_on_arrivals(self):
        result = evaluate('(()=>{renderMilestones(messages.slice(0,1));const first=host.children[0];open("Synthetic");const opened=host.scrollTop;host.scrollTop=35;renderMilestones(messages);const during=host.scrollTop;close.onclick();timers.shift()();return {opened,during,top:host.scrollTop,inert:rail.inert,retained:host.children[0]===first,count:host.children.length,detailHidden:detail.hidden};})()', [message('one'), message('two')])
        self.assertEqual(result, {'opened': 70, 'during': 35, 'top': 35, 'inert': False, 'retained': True, 'count': 2, 'detailHidden': True})

    def test_detail_keeps_latest_follow_and_reopening_cancels_pending_close(self):
        result = evaluate('(()=>{host.scrollTop=400;renderMilestones(messages.slice(0,1));open("Synthetic");const opened=host.scrollTop;renderMilestones(messages);const during=host.scrollTop;close.onclick();open("Another selection");timers.shift()();const reopened={inert:rail.inert,hidden:detail.hidden,top:host.scrollTop};close.onclick();timers.shift()();return {opened,during,reopened,top:host.scrollTop,inert:rail.inert,detailHidden:detail.hidden};})()', [message('one'), message('two')])
        self.assertEqual(result, {'opened': 500, 'during': 500, 'reopened': {'inert': False, 'hidden': False, 'top': 500}, 'top': 500, 'inert': False, 'detailHidden': True})


if __name__ == '__main__':
    unittest.main()
