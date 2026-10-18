"""New synthetic frontend regressions for logical storyline grouping.

These test presentation only. They never execute tools or model calls.
"""
import subprocess
import unittest
from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[1] / 'web' / 'flow.js').read_text()


def helper(name, following):
    return 'function ' + name + SOURCE.split('function ' + name, 1)[1].split('function ' + following, 1)[0]


def node(script):
    result = subprocess.run(['node', '-e', script], text=True, capture_output=True)
    if result.returncode:
        raise AssertionError(result.stderr)


class LogicalStorylineTests(unittest.TestCase):
    def test_dynamic_workers_models_annotations_and_source_diagnostic(self):
        script = helper('storyLaneView(', 'inspectStoryActivity(')
        script += r'''
const assert=require('node:assert/strict');
for(const workers of [0,1,2,7])for(const models of [0,1,3]){
  const lanes=[],activities=[],logicalLanes=[],mapping={};
  for(const [kind,count] of [['worker',workers],['model',models]])for(let i=0;i<count;i++){
    const id=kind+':'+i,source='raw:'+id;
    lanes.push({id:source,label:'Session '+i});
    logicalLanes.push({id,label:kind+' '+i,kind});
    const activity={id:'activity:'+id,lane_id:source,start_ms:i*100,end_ms:i*100+50,timing:'interval'};
    activities.push(activity);mapping[activity.id]=id;
  }
  for(const kind of ['task','run','setup']){
    lanes.push({id:'raw:'+kind,label:kind+' record'});
    activities.push({id:'activity:'+kind,lane_id:'raw:'+kind,start_ms:0,end_ms:0,timing:'instant'});
  }
  const story={lanes,activities},flow={logical_storyline:{lanes:logicalLanes,activity_lanes:mapping,
    annotations:['task','run','setup'].map(kind=>({activity_id:'activity:'+kind,kind,label:kind}))}};
  const before=JSON.stringify([story,flow]),logical=storyLaneView(story,flow,'logical');
  assert.equal(logical.mode,'logical');assert.equal(logical.lanes.length,workers+models);
  assert.deepEqual(logical.annotations.map(a=>a.id),['activity:task','activity:run','activity:setup']);
  for(const lane of logical.lanes){assert.equal(lane.activities.length,1);assert.equal(mapping[lane.activities[0].id],lane.id);}
  const raw=storyLaneView(story,flow,'source');
  assert.equal(raw.lanes.length,workers+models+3);assert.equal(raw.annotations.length,0);
  assert.deepEqual(raw.lanes.map(lane=>lane.activities.map(a=>a.id)),lanes.map(lane=>activities.filter(a=>a.lane_id===lane.id).map(a=>a.id)));
  assert.equal(JSON.stringify([story,flow]),before,'presentation grouping must not rewrite evidence');
}
'''
        node(script)

    def test_target_contacts_require_explicit_mapping_and_keep_recorded_anchor(self):
        script = helper('storyLaneView(', 'inspectStoryActivity(')
        script += r'''
const assert=require('node:assert/strict');
const activity={id:'tool',lane_id:'raw',start_ms:100,end_ms:900,timing:'interval',label:'Synthetic request'};
const story={lanes:[{id:'raw',label:'Raw source'}],activities:[activity]};
const flow={logical_storyline:{lanes:[{id:'worker',kind:'worker'},{id:'target',kind:'target'}],activity_lanes:{tool:'worker'},activity_entities:{tool:{entity_id:'worker',target_entity_id:'target',target_ms:850}}}};
let view=storyLaneView(story,flow,'logical');
assert.equal(view.lanes[0].activities[0],activity);
assert.deepEqual(view.lanes[1].contacts,[{activity,anchor:850,label:'Contact · Synthetic request'}]);
delete flow.logical_storyline.activity_entities.tool.target_ms;
assert.equal(storyLaneView(story,flow,'logical').lanes[1].contacts[0].anchor,900);
delete flow.logical_storyline.activity_entities.tool.target_entity_id;
assert.deepEqual(storyLaneView(story,flow,'logical').lanes[1].contacts,[],'target activity cannot come from label or command heuristics');
view=storyLaneView(story,{},'logical');assert.equal(view.mode,'source');assert.equal(view.available,false);
assert.equal(view.lanes[0].activities[0],activity,'older portable recordings retain source view');
'''
        node(script)

    def test_rebuilt_dependency_control_reflects_retained_state(self):
        self.assertIn("check.checked=allDependencies", SOURCE)

    def test_render_modes_keep_timing_markers_and_dependency_endpoints(self):
        script = r'''
const assert=require('node:assert/strict');
class Element{
  constructor(tag,text,cls){this.tag=tag;this.text=text===undefined?'':String(text);this.className=cls||'';this.children=[];this.dataset={};this.style={setProperty:(key,value)=>{this.style[key]=value;}};this.attributes={};this.namespaceURI='http://www.w3.org/2000/svg';}
  get classList(){return {contains:name=>this.className.split(' ').includes(name),add:name=>{if(!this.classList.contains(name))this.className+=' '+name;},toggle:(name,on)=>{this.className=this.className.split(' ').filter(c=>c!==name).join(' ');if(on)this.classList.add(name);}};}
  get firstChild(){return this.children[0];}
  get textContent(){return this.text+this.children.map(c=>c.textContent).join('');}
  set textContent(text){this.text=String(text);this.children=[];}
  append(...children){for(const child of children){child.parentElement=this;this.children.push(child);}}
  prepend(child){child.parentElement=this;this.children.unshift(child);}
  replaceChildren(...children){this.text='';this.children=[];this.append(...children);}
  setAttribute(key,value){this.attributes[key]=String(value);}
  querySelectorAll(selector){return this.children.flatMap(c=>[...(c.classList.contains(selector.slice(1))?[c]:[]),...c.querySelectorAll(selector)]);}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  getBoundingClientRect(){
    if(this.classList.contains('f-story-track'))return {left:138,top:100,width:1000,height:100};
    if(this.classList.contains('f-story-clip'))return {left:138+parseFloat(this.style.left)*10,top:100+parseFloat(this.style.top),width:10,height:27};
    return {left:0,top:0,width:1138,height:500};
  }
}
const n=(tag,text,cls)=>new Element(tag,text,cls),btn=(text,fn,cls)=>{const el=n('button',text,cls);el.onclick=fn;return el;};
const document={createElementNS:(ns,tag)=>new Element(tag),createTextNode:text=>n('text',text)};
const getComputedStyle=()=>({height:'32px'});
const storyHost=n('div'),controls=new Map(),$=id=>id==='lanes'?storyHost:controls.get(id);
for(const id of ['time-mode','story-lanes-logical','story-lanes-source'])controls.set(id,n('span'));
const activities=[
  {id:'task',lane_id:'raw-task',label:'Original task',category:'observation',start_ms:0,end_ms:0,timing:'instant'},
  {id:'setup',lane_id:'raw-task',label:'Goal initializer',category:'model',start_ms:0,end_ms:700,timing:'interval'},
  {id:'model',lane_id:'raw-worker',label:'Model exchange',category:'model',start_ms:100,end_ms:700,timing:'interval'},
  {id:'tool',lane_id:'raw-worker',label:'Synthetic request',category:'tool',start_ms:1000,end_ms:9000,timing:'interval'},
  {id:'usage',lane_id:'raw-worker',label:'activity record',category:'observation',start_ms:9001,end_ms:9001,timing:'instant'}
];
const story={duration_ms:10000,lanes:[{id:'raw-task',label:'Task source'},{id:'raw-worker',label:'Worker source'}],activities,
 edges:[{kind:'dependency',provenance:'recorded',source_activity_id:'model',target_activity_id:'tool',source_ms:100,target_ms:1000,label:'Recorded dependency'}]};
const f={logical_storyline:{lanes:[{id:'worker',label:'Worker 1',kind:'worker'},{id:'model-entity',label:'Any model',kind:'model'},{id:'target',label:'Local target',kind:'target'}],
 activity_lanes:{model:'model-entity',tool:'worker',usage:'worker'},activity_entities:{tool:{target_entity_id:'target',target_ms:8500}},
 activity_groups:[
  {id:'group:model',entity_id:'model-entity',label:'Model exchange',category:'model',timing:'interval',start_ms:100,end_ms:700,source_activity_ids:['model'],meaningful_activity_ids:['model'],provenance:'presentation'},
  {id:'group:tool',entity_id:'worker',label:'Synthetic request',category:'tool',timing:'interval',start_ms:1000,end_ms:9000,source_activity_ids:['tool','usage'],meaningful_activity_ids:['tool'],provenance:'presentation'}],
 annotations:[{activity_id:'task',kind:'task',label:'Original task'},{activity_id:'setup',kind:'setup',label:'Goal initializer'}]}};
const messages=[{id:'result',activity_id:'tool',semantic_tags:[{kind:'success',label:'Recorded result',event_ids:['result']}]}];
const duration=10000,position=10000,clock=value=>String(value),playing=false,storyFollow=false,untimed=false;
const storyViewport={clientWidth:1138,scrollLeft:0};
const storyGeometry=()=>({label:138,width:(parseFloat(storyHost.style.width)||1138)-138}),storyFraction=()=>1,setStoryScroll=value=>{storyViewport.scrollLeft=Math.max(0,value);};
const storyDisplayDuration=()=>10000,storyRecordedAt=value=>value,storyDisplayAt=value=>value;
const storyAxisFraction=value=>Math.max(0,Math.min(1,(value??0)/10000)),storyAxisSpan=(start,end)=>Math.max(0,end-start),storyIdleBreaks=[];
let storyZoom=1,storyDrag=null;
let storyLaneMode='logical',storyAxisMode='condensed',dependencySvg=null,allDependencies=true,selectedActivity=null;
const buildStoryMarkerLegend=()=>{},buildDependencies=()=>{dependencySvg=n('svg');storyHost.append(dependencySvg);},inspectStoryActivity=()=>{},inspectEdge=()=>{};
'''
        script += helper('storyLaneView(', 'inspectStoryActivity(')
        script += helper('appendStoryRow(', 'buildStory(')
        script += helper('buildStory(', 'setupStoryLaneModes(')
        script += helper('drawDependencies(', 'buildDependencies(')
        script += helper('sizeStory(', 'setupStoryNavigation(')
        script += '\n'.join(line for line in SOURCE.splitlines() if line.startswith((
            'function storyMarkerEntries(', 'function markerLabel(', 'function markStoryClip(',
        )))
        script += r'''
for(const mode of ['logical','source','logical']){
  storyLaneMode=mode;buildStory();for(const zoom of [1,4,1])sizeStory(zoom);drawDependencies();
  assert.equal(storyHost.dataset.laneMode,mode);
  const rows=storyHost.querySelectorAll('.f-story-row'),clips=storyHost.querySelectorAll('.f-story-clip');
  assert.equal(rows.length,mode==='logical'?4:2);
  if(mode==='logical'){
    assert.equal(rows[0].dataset.laneId,'worker');assert.equal(rows.at(-1).dataset.laneKind,'annotation');
    for(const row of rows)assert.equal(row.querySelector('.f-story-track').dataset.tiers,'1','logical entities have one visual tier');
    assert.equal(clips.filter(c=>c.dataset.contact!=='true').length,4);
    assert.deepEqual(JSON.parse(clips.find(c=>c.dataset.id==='group:tool').dataset.sourceActivityIds),['tool','usage']);
  }else for(const activity of activities){
    const primary=clips.filter(c=>c.dataset.id===activity.id&&c.dataset.contact!=='true');
    assert.equal(primary.length,1,'source mode retains each observed activity anchor');
    assert.equal(primary[0].style.left,(activity.start_ms??activity.end_ms)/10000*100+'%');
  }
  const tool=clips.find(c=>c.dataset.id===(mode==='logical'?'group:tool':'tool')&&c.dataset.contact!=='true');
  assert.equal(tool.querySelector('.f-story-marker').textContent,'💡');
  assert.ok(tool.attributes['aria-label'].includes('Recorded result'));
  const contact=clips.find(c=>c.dataset.contact==='true');
  assert.equal(Boolean(contact),mode==='logical');if(contact)assert.equal(contact.style.left,'85%');
  assert.equal(dependencySvg.children.length,2);
  assert.ok(dependencySvg.children[0].attributes.d.startsWith('M 148 113 C '),'dependency uses its exact recorded source event time, not model activity end or target contact');
  assert.ok(dependencySvg.children[0].attributes.d.endsWith(', 238 '+(113+parseFloat(tool.style.top))));
}
'''
        node(script)


if __name__ == '__main__':
    unittest.main()
