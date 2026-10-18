"""Synthetic direct-scrubbing regressions; recorded content is never executed."""
from pathlib import Path
import subprocess
import unittest

SOURCE=(Path(__file__).resolve().parents[1]/'web/flow.js').read_text()
HELPERS='function storySeekRecorded('+SOURCE.split('function storySeekRecorded(',1)[1].split('function finishStoryDrag(',1)[0]

class StorylineScrubTests(unittest.TestCase):
    def run_js(self, code):
        result=subprocess.run(['node','-e',code],text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_smart_inverse_handles_holds_gaps_and_end_without_previous_visibility(self):
        self.run_js(HELPERS+r'''
const assert=require('node:assert/strict');
const duration=130000,viewingDuration=13000;
const segments=[
 {start_ms:0,end_ms:1000,recorded_start_ms:0,recorded_end_ms:0},
 {start_ms:1000,end_ms:3000,recorded_start_ms:0,recorded_end_ms:10000},
 {start_ms:3000,end_ms:5000,recorded_start_ms:10000,recorded_end_ms:10000},
 {start_ms:5000,end_ms:6000,recorded_start_ms:10000,recorded_end_ms:10000},
 {start_ms:6000,end_ms:10000,recorded_start_ms:10000,recorded_end_ms:120000},
 {start_ms:10000,end_ms:12000,recorded_start_ms:120000,recorded_end_ms:130000},
 {start_ms:12000,end_ms:13000,recorded_start_ms:130000,recorded_end_ms:130000}];
let smart=true,value;const isSmart=()=>smart,seek=x=>value=x;
for(const [recorded,viewing] of [[65000,8000],[10000,3000],[0,0],[130000,13000],[-1,0],[150000,13000],[5000,2000]]){
 storySeekRecorded(recorded);assert.equal(value,viewing);
}
smart=false;storySeekRecorded(65000);assert.equal(value,65000);
''')

    def test_pointer_uses_scrolled_track_geometry_and_condensed_axis(self):
        self.run_js(HELPERS+r'''
const assert=require('node:assert/strict');
const duration=130000,viewingDuration=1,segments=[],isSmart=()=>false;
let value;const seek=x=>value=x;
const storyHost={querySelector:()=>({getBoundingClientRect:()=>({left:-200,width:1000})})};
const storyDisplayDuration=()=>25000;
const storyRecordedAt=x=>x<=10000?x:x<=15000?10000+(x-10000)*22:x+105000;
storySeekPointer({clientX:300});assert.equal(value,65000);
storySeekPointer({clientX:-500});assert.equal(value,0);
storySeekPointer({clientX:1000});assert.equal(value,130000);
''')

if __name__=='__main__':unittest.main()
