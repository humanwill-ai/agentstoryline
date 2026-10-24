"""Synthetic frontend regressions for evidence-anchored marker redraws."""
import subprocess
import unittest
from pathlib import Path


class StorylineMarkerTests(unittest.TestCase):
    def test_global_marker_legend_is_absent_while_clip_markers_remain(self):
        source = (Path(__file__).resolve().parents[1] / 'web' / 'flow.js').read_text()
        self.assertNotIn('buildStoryMarkerLegend', source)
        self.assertIn('function markStoryClip(', source)

    def test_sizing_resize_and_zoom_keep_markers_on_their_activity(self):
        source = (Path(__file__).resolve().parents[1] / 'web' / 'flow.js').read_text()
        size = source.split('function sizeStory(', 1)[1].split('function setupStoryNavigation(', 1)[0]
        helpers = '\n'.join(line for line in source.splitlines() if line.startswith((
            'function storyMarkerEntries(', 'function markerLabel(', 'function markStoryClip(',
        )))
        script = r'''
const assert=require('node:assert/strict');
class Element {
  constructor(text='',cls='') {this.children=[];this.text=text;this.className=cls;this.style={};this.dataset={};this.attributes={};}
  get classList() {return {
    contains:name=>this.className.split(' ').includes(name),
    add:name=>{if(!this.classList.contains(name))this.className+=' '+name;},
    toggle:(name,on)=>{this.className=this.className.split(' ').filter(c=>c!==name).join(' ');if(on)this.classList.add(name);}
  };}
  get firstChild() {return this.children[0];}
  get textContent() {return this.text+this.children.map(c=>c.textContent).join('');}
  set textContent(text) {this.text=String(text);this.children=[];}
  append(...children) {for(const child of children){child.parentElement=this;this.children.push(child);}}
  prepend(child) {child.parentElement=this;this.children.unshift(child);}
  replaceChildren(...children) {this.text='';this.children=[];this.append(...children);}
  setAttribute(key,value) {this.attributes[key]=String(value);}
  querySelectorAll(selector) {return this.children.flatMap(c=>[
    ...(c.classList.contains(selector.slice(1))?[c]:[]),...c.querySelectorAll(selector)
  ]);}
  querySelector(selector) {return this.querySelectorAll(selector)[0]||null;}
}
const document={createTextNode:text=>new Element(text)};
const n=(tag,text,cls)=>new Element(text,cls);
const activities=[
  {id:'token',label:'Token result',category:'tool',timing:'instant',start_ms:1000,end_ms:1000},
  {id:'request',label:'Document request',category:'tool',timing:'interval',start_ms:4500,end_ms:4800},
  {id:'endpoint',label:'Final result',category:'tool',timing:'incomplete',start_ms:null,end_ms:10000},
  {id:'plain',label:'Ordinary observation',category:'observation',timing:'instant',start_ms:4700,end_ms:4700}
];
const messages=[
  {id:'token-result',activity_id:'token',semantic_tags:[{kind:'success',label:'Token obtained',event_ids:['token-result']}]},
  {id:'attempt',activity_id:'request',semantic_tags:[{kind:'danger',label:'Exploitation attempt',event_ids:['denial','token-result','attempt']}]},
  {id:'document-result',activity_id:'request',semantic_tags:[{kind:'success',label:'Document retrieved',event_ids:['document-result']}]},
  {id:'final-result',activity_id:'endpoint',semantic_tags:[{kind:'success',label:'Recorded attempt succeeded',event_ids:['final-result']}]},
  {id:'plain-message',activity_id:'plain',text:'success and exploitation'}
];
const story={duration_ms:10000},duration=10000,position=0,playing=false,storyFollow=false,untimed=false;
const storyHost=new Element(),track=new Element('','f-story-track'),ticks=new Element('','f-story-ticks');
storyHost.append(track,ticks);
const storyViewport={clientWidth:1138,scrollLeft:0};
const storyGeometry=()=>({label:138,width:(parseFloat(storyHost.style.width)||1138)-138});
const storyFraction=()=>0;
const storyDisplayDuration=()=>10000,storyRecordedAt=value=>value,storyDisplayAt=value=>value;
const storyAxisFraction=value=>Math.max(0,Math.min(1,(value??0)/10000)),storyAxisSpan=(start,end)=>Math.max(0,end-start),storyIdleBreaks=[];
const setStoryScroll=value=>{storyViewport.scrollLeft=Math.max(0,value);};
const clock=value=>String(value),$=()=>null,drawDependencies=()=>{};
let storyZoom=1,storyAxisMode='condensed';
'''
        # This marker harness renders Story with no telemetry attachment.
        script += "\nconst telemetryMode='story',authoritative={};\n"
        script += 'function decorateTelemetryClip(' + source.split('function decorateTelemetryClip(', 1)[1].split('function inspectStoryActivity(', 1)[0]
        script += helpers + '\nfunction sizeStory(' + size
        script += r'''
for(const [i,a] of activities.entries()){
  const clip=n('button',a.label,'f-story-clip');
  clip.dataset.id=a.id;clip.style.left=(a.start_ms??a.end_ms)/story.duration_ms*100+'%';
  clip.style.top=i===3?'27px':'0px';
  clip.title=a.label+' · '+a.category+' · '+a.timing;
  markStoryClip(clip,a);track.append(clip);
}
const clips=track.querySelectorAll('.f-story-clip'),anchors=clips.map(c=>[c.style.left,c.style.top]);
function check(){
  assert.deepEqual(clips.map(c=>[c.style.left,c.style.top]),anchors,'recorded anchors must not move');
  for(const clip of clips){
    const expected=messages.filter(m=>m.activity_id===clip.dataset.id).flatMap(m=>m.semantic_tags||[]);
    assert.equal(clip.querySelectorAll('.f-story-marker').length,expected.length?1:0,'marker belongs to its activity');
    if(!expected.length)continue;
    for(const tag of expected){
      assert.ok(clip.querySelector('.f-story-marker').textContent.includes(tag.kind==='danger'?'⚠':'💡'));
      assert.ok(clip.attributes['aria-label'].includes(tag.label));
    }
    if(clip.classList.contains('f-story-point')){
      const caption=clip.querySelector('.f-story-marker-caption');
      assert.ok(caption,'point keeps evidence-derived label');
      assert.ok(parseFloat(caption.style.maxWidth)>0,'nearby ordinary points do not erase semantic captions');
    }
  }
}
sizeStory();check(); // Initial setup performs responsive sizing immediately.
const titles=clips.map(c=>c.title);
assert.ok(clips[1].classList.contains('f-story-point'));
assert.ok(clips[2].classList.contains('f-point-end'));
for(const width of [500,960,1138]){storyViewport.clientWidth=width;sizeStory();check();}
sizeStory(4);check();
assert.ok(!clips[1].classList.contains('f-story-point'),'zoom expands a short measured interval');
sizeStory(1);check();
assert.ok(clips[1].classList.contains('f-story-point'),'Fit restores its point representation');
assert.deepEqual(clips.map(c=>c.title),titles,'redraws must not duplicate marker descriptions');
assert.equal(clips[1].querySelector('.f-story-marker').textContent,'⚠ 💡');
'''
        subprocess.run(['node', '-e', script], check=True, text=True, capture_output=True)
