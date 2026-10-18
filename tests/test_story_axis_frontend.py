"""Frontend regressions for the presentation-only condensed storyline axis."""
import subprocess
import unittest
from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[1] / "web" / "flow.js").read_text()


def node(script):
    result = subprocess.run(["node", "-e", script], text=True, capture_output=True)
    if result.returncode:
        raise AssertionError(result.stderr)


AXIS_HELPERS = "function storyAxisFromFlow" + SOURCE.split(
    "function storyAxisFromFlow", 1
)[1].split("function initialize", 1)[0]


class StoryAxisFrontendTests(unittest.TestCase):
    def test_recorded_axis_segments_are_consumed_without_rewriting_source_time(self):
        node(AXIS_HELPERS + r"""
const assert=require('node:assert/strict');
const supplied={version:1,mode:'condensed_idle',recorded_duration_ms:120000,display_duration_ms:65000,threshold_ms:30000,compact_idle_ms:5000,segments:[
  {kind:'recorded',recorded_start_ms:0,recorded_end_ms:40000,display_start_ms:0,display_end_ms:40000},
  {kind:'idle_break',recorded_start_ms:40000,recorded_end_ms:100000,display_start_ms:40000,display_end_ms:45000,omitted_ms:55000,label:'01:00 with no recorded activity · condensed'},
  {kind:'recorded',recorded_start_ms:100000,recorded_end_ms:120000,display_start_ms:45000,display_end_ms:65000}
]};
const axis=storyAxisFromFlow({story_axis:supplied},{duration_ms:120000},[],[],120000);
assert.equal(axis.source,'recorded');assert.equal(axis.display_duration_ms,65000);
assert.equal(storyAxisDisplayAt(axis,0),0);assert.equal(storyAxisDisplayAt(axis,40000),40000);
assert.equal(storyAxisDisplayAt(axis,70000),42500);assert.equal(storyAxisDisplayAt(axis,100000),45000);
assert.equal(storyAxisDisplayAt(axis,120000),65000);
assert.equal(storyAxisRecordedAt(axis,42500),70000);assert.equal(storyAxisRecordedAt(axis,65000),120000);
assert.equal(supplied.recorded_duration_ms,120000);assert.equal(supplied.segments[1].recorded_end_ms,100000);
""")

    def test_supplied_axis_duration_mismatch_falls_back_to_source_clock(self):
        node(AXIS_HELPERS + r"""
const assert=require('node:assert/strict');
const mismatched={version:1,recorded_duration_ms:130000,display_duration_ms:75000,segments:[
  {kind:'recorded',recorded_start_ms:0,recorded_end_ms:40000,display_start_ms:0,display_end_ms:40000},
  {kind:'idle_break',recorded_start_ms:40000,recorded_end_ms:100000,display_start_ms:40000,display_end_ms:45000},
  {kind:'recorded',recorded_start_ms:100000,recorded_end_ms:130000,display_start_ms:45000,display_end_ms:75000}
]};
const messages=[{offset_ms:0},{offset_ms:120000}];
const axis=storyAxisFromFlow({duration_ms:120000,story_axis:mismatched},{duration_ms:120000},messages,[],120000);
assert.equal(axis.source,'fallback');assert.equal(axis.recorded_duration_ms,120000,'source duration is authoritative');
assert.equal(axis.segments.at(-1).recorded_end_ms,120000);
assert.equal(axis.segments[0].kind,'idle_break');
""")

    def test_fallback_uses_longest_source_clock_and_keeps_later_evidence(self):
        node(AXIS_HELPERS + r"""
const assert=require('node:assert/strict');
const stale={version:1,recorded_duration_ms:100000,display_duration_ms:100000,segments:[
  {kind:'recorded',recorded_start_ms:0,recorded_end_ms:100000,display_start_ms:0,display_end_ms:100000}
]};
const messages=[{offset_ms:50000},{offset_ms:1000000}];
const axis=storyAxisFromFlow({duration_ms:1000000,story_axis:stale},{duration_ms:100000},messages,[],100000);
assert.equal(axis.source,'fallback','disagreeing source clocks reject supplied presentation metadata');
assert.equal(axis.recorded_duration_ms,1000000,'fallback uses the longest finite source clock');
assert.equal(axis.segments.at(-1).recorded_end_ms,1000000);
assert.equal(storyAxisDisplayAt(axis,1000000),axis.display_duration_ms,'later evidence is not clamped to the shorter storyline duration');
assert.equal(storyAxisRecordedAt(axis,axis.display_duration_ms),1000000);
""")

    def test_supplied_segment_scale_and_source_interval_guards(self):
        node(AXIS_HELPERS + r"""
const assert=require('node:assert/strict');
const base={version:1,recorded_duration_ms:120000,display_duration_ms:65000,segments:[
  {kind:'recorded',recorded_start_ms:0,recorded_end_ms:40000,display_start_ms:0,display_end_ms:40000},
  {kind:'idle_break',recorded_start_ms:40000,recorded_end_ms:100000,display_start_ms:40000,display_end_ms:45000},
  {kind:'recorded',recorded_start_ms:100000,recorded_end_ms:120000,display_start_ms:45000,display_end_ms:65000}
]};
const source={duration_ms:120000},messages=[{offset_ms:0},{offset_ms:40000},{offset_ms:100000},{offset_ms:120000}];
let malformed=structuredClone(base);malformed.segments[0].display_end_ms=39000;malformed.segments[1].display_start_ms=39000;malformed.display_duration_ms=64000;malformed.segments[1].display_end_ms=44000;malformed.segments[2].display_start_ms=44000;malformed.segments[2].display_end_ms=64000;
assert.equal(storyAxisFromFlow({duration_ms:120000,story_axis:malformed},source,messages,[],120000).source,'fallback','recorded segments must retain 1:1 scale');
malformed=structuredClone(base);malformed.segments[1].display_end_ms=105000;malformed.segments[2].display_start_ms=105000;malformed.segments[2].display_end_ms=125000;malformed.display_duration_ms=125000;
assert.equal(storyAxisFromFlow({duration_ms:120000,story_axis:malformed},source,messages,[],120000).source,'fallback','idle breaks cannot expand time');
const interval=[{timing:'interval',start_ms:50000,end_ms:90000}];
const guarded=storyAxisFromFlow({duration_ms:120000,story_axis:base},source,messages,interval,120000);
assert.equal(guarded.source,'fallback','supplied idle breaks cannot cross a reliable source interval');
assert.equal(guarded.segments.some(segment=>segment.kind==='idle_break'&&segment.recorded_start_ms===40000&&segment.recorded_end_ms===100000),false);
""")

    def test_supplied_idle_break_cannot_hide_source_points(self):
        node(AXIS_HELPERS + r"""
const assert=require('node:assert/strict');
const supplied={version:1,recorded_duration_ms:120000,display_duration_ms:65000,segments:[
  {kind:'recorded',recorded_start_ms:0,recorded_end_ms:40000,display_start_ms:0,display_end_ms:40000},
  {kind:'idle_break',recorded_start_ms:40000,recorded_end_ms:100000,display_start_ms:40000,display_end_ms:45000},
  {kind:'recorded',recorded_start_ms:100000,recorded_end_ms:120000,display_start_ms:45000,display_end_ms:65000}
]};
const source={duration_ms:120000},boundaryMessages=[{offset_ms:40000},{offset_ms:100000}];
let axis=storyAxisFromFlow({duration_ms:120000,story_axis:supplied},source,[...boundaryMessages,{offset_ms:70000}],[],120000);
assert.equal(axis.source,'fallback','a supplied break cannot hide a message arrival');
assert.equal(axis.segments.some(segment=>segment.kind==='idle_break'&&segment.recorded_start_ms===40000&&segment.recorded_end_ms===100000),false);
axis=storyAxisFromFlow({duration_ms:120000,story_axis:supplied},source,boundaryMessages,[{timing:'instant',start_ms:70000,end_ms:70000}],120000);
assert.equal(axis.source,'fallback','a supplied break cannot hide an activity boundary');
assert.equal(axis.segments.some(segment=>segment.kind==='idle_break'&&segment.recorded_start_ms===40000&&segment.recorded_end_ms===100000),false);
axis=storyAxisFromFlow({duration_ms:120000,story_axis:supplied},source,boundaryMessages,[{timing:'instant',start_ms:40000,end_ms:100000}],120000);
assert.equal(axis.source,'recorded','source points exactly on break boundaries remain valid');
""")

    def test_dependency_and_target_contact_anchors_split_idle_breaks(self):
        node(AXIS_HELPERS + r"""
const assert=require('node:assert/strict');
const supplied={version:1,recorded_duration_ms:120000,display_duration_ms:65000,segments:[
  {kind:'recorded',recorded_start_ms:0,recorded_end_ms:40000,display_start_ms:0,display_end_ms:40000},
  {kind:'idle_break',recorded_start_ms:40000,recorded_end_ms:100000,display_start_ms:40000,display_end_ms:45000},
  {kind:'recorded',recorded_start_ms:100000,recorded_end_ms:120000,display_start_ms:45000,display_end_ms:65000}
]};
const messages=[{offset_ms:40000},{offset_ms:100000}];
let story={duration_ms:120000,edges:[{source_ms:40000,target_ms:70000}]};
let axis=storyAxisFromFlow({duration_ms:120000,story_axis:supplied},story,messages,[],120000);
assert.equal(axis.source,'fallback','an interior dependency timestamp invalidates a supplied idle break');
assert.equal(axis.segments.some(segment=>segment.kind==='idle_break'&&segment.recorded_start_ms===40000&&segment.recorded_end_ms===100000),false);
assert.ok(axis.segments.some(segment=>segment.recorded_start_ms===70000||segment.recorded_end_ms===70000),'fallback splits at the dependency timestamp');
story={duration_ms:120000,edges:[]};
axis=storyAxisFromFlow({duration_ms:120000,story_axis:supplied,logical_storyline:{activity_entities:{tool:{target_ms:70000}}}},story,messages,[],120000);
assert.equal(axis.source,'fallback','an interior target-contact timestamp invalidates a supplied idle break');
assert.ok(axis.segments.some(segment=>segment.recorded_start_ms===70000||segment.recorded_end_ms===70000),'fallback splits at the target-contact timestamp');
story={duration_ms:120000,edges:[{source_ms:40000,target_ms:100000}]};
axis=storyAxisFromFlow({duration_ms:120000,story_axis:supplied,logical_storyline:{activity_entities:{tool:{target_ms:100000}}}},story,messages,[],120000);
assert.equal(axis.source,'recorded','dependency and contact anchors on break boundaries remain valid');
""")

    def test_post_gap_contact_and_dependency_coordinates_change_only_by_axis_mode(self):
        node(AXIS_HELPERS + r"""
const assert=require('node:assert/strict');
const axis=storyAxisFromFlow({duration_ms:120000,story_axis:{version:1,recorded_duration_ms:120000,display_duration_ms:65000,segments:[
  {kind:'recorded',recorded_start_ms:0,recorded_end_ms:40000,display_start_ms:0,display_end_ms:40000},
  {kind:'idle_break',recorded_start_ms:40000,recorded_end_ms:100000,display_start_ms:40000,display_end_ms:45000},
  {kind:'recorded',recorded_start_ms:100000,recorded_end_ms:120000,display_start_ms:45000,display_end_ms:65000}
]}},{duration_ms:120000},[],[],120000);
const condensed=time=>storyAxisDisplayAt(axis,time)/axis.display_duration_ms*100,recorded=time=>time/axis.recorded_duration_ms*100;
const contactTime=110000,dependencySource=30000,dependencyTarget=110000;
assert.equal(condensed(contactTime),condensed(dependencyTarget),'contact and dependency target share one mapper');
assert.ok(Math.abs(condensed(contactTime)-84.61538461538461)<1e-9);
assert.ok(Math.abs(recorded(contactTime)-91.66666666666666)<1e-9);
assert.ok(Math.abs(condensed(dependencySource)-46.15384615384615)<1e-9);
assert.equal(recorded(dependencySource),25);
assert.equal(storyAxisRecordedAt(axis,storyAxisDisplayAt(axis,contactTime)),contactTime,'mapped post-gap time remains reversible');
""")

    def test_legacy_fallback_condenses_only_uncovered_gaps_of_at_least_30_seconds(self):
        node(AXIS_HELPERS + r"""
const assert=require('node:assert/strict');
const messages=[{offset_ms:0},{offset_ms:60000},{offset_ms:90000},{offset_ms:150000}];
const instants=[0,60000,90000,150000].map((time,index)=>({id:String(index),timing:'instant',start_ms:time,end_ms:time}));
let axis=storyAxisFromFlow({}, {duration_ms:150000}, messages, instants, 150000);
let breaks=axis.segments.filter(segment=>segment.kind==='idle_break');
assert.deepEqual(breaks.map(gap=>[gap.recorded_start_ms,gap.recorded_end_ms]),[[0,60000],[60000,90000],[90000,150000]]);
assert.ok(breaks.every(gap=>gap.display_end_ms-gap.display_start_ms===5000));
assert.equal(axis.threshold_ms,30000);assert.equal(axis.compact_idle_ms,5000);assert.equal(axis.display_duration_ms,15000);
const covered=[...instants,{id:'model',timing:'interval',start_ms:0,end_ms:60000}];
axis=storyAxisFromFlow({}, {duration_ms:150000}, messages, covered, 150000);
breaks=axis.segments.filter(segment=>segment.kind==='idle_break');
assert.deepEqual(breaks.map(gap=>[gap.recorded_start_ms,gap.recorded_end_ms]),[[60000,90000],[90000,150000]],'a recorded interval prevents false idle classification');
const short=storyAxisFromFlow({}, {duration_ms:29999}, [{offset_ms:0},{offset_ms:29999}], [], 29999);
assert.equal(short.segments.some(segment=>segment.kind==='idle_break'),false);
""")

    def test_all_storyline_geometry_uses_the_shared_axis_mapping(self):
        self.assertIn("c.style.left=storyFraction()*100+'%'", SOURCE)
        self.assertIn("clip.style.left=storyAxisFraction(start)*100+'%'", SOURCE)
        self.assertIn("storyAxisSpan(start,activity.end_ms??start)", SOURCE)
        self.assertIn("storyAxisFraction(sm)*sr.width", SOURCE)
        self.assertIn("storyAxisFraction(tm)*tr.width", SOURCE)
        self.assertIn("const displayMs=storyDisplayDuration()*i/count,recordedMs=storyRecordedAt(displayMs)", SOURCE)
        self.assertIn("const storyAxisFraction=recordedMs=>", SOURCE)

    def test_axis_control_and_break_are_independent_accessible_controls(self):
        self.assertIn("aria-label','Storyline time axis", SOURCE)
        self.assertIn("[['condensed','Condensed'],['recorded','Recorded']]", SOURCE)
        self.assertIn("storyAxisMode:document.getElementById('lanes').dataset.axisMode||'condensed'", SOURCE)
        self.assertIn("with no recorded activity", SOURCE)
        self.assertIn("button.setAttribute('aria-controls','detail')", SOURCE)
        self.assertIn("Presentation-only condensation; original timestamps and evidence are unchanged.", SOURCE)
        self.assertNotIn("storyAxisMode=pacingMode", SOURCE)


if __name__ == "__main__":
    unittest.main()
