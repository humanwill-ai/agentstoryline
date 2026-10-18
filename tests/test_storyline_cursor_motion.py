"""Synthetic cursor motion regressions; evidence clocks remain authoritative."""
import subprocess
import unittest
from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[1] / "web" / "flow.js").read_text()

SCHEDULE = r"""
const assert = require('node:assert/strict');
let smart = true, untimed = false, position = -1, reviewPosition = -1;
let storyDrag = null, storyCursorAnchor = null;
const isSmart = () => smart;
const storyDisplayDuration = () => 220000;
const storyGeometry = () => ({label:130, width:1100});
const segments = [
  {kind:'lead',start_ms:0,end_ms:400,recorded_start_ms:0,recorded_end_ms:0},
  {kind:'hold',start_ms:400,end_ms:1400,recorded_start_ms:0,recorded_end_ms:0},
  {kind:'hold',start_ms:1400,end_ms:3400,recorded_start_ms:0,recorded_end_ms:0},
  {kind:'gap',start_ms:3400,end_ms:4400,recorded_start_ms:0,recorded_end_ms:10000},
  {kind:'hold',start_ms:4400,end_ms:5400,recorded_start_ms:10000,recorded_end_ms:10000},
  {kind:'gap',start_ms:5400,end_ms:6400,recorded_start_ms:10000,recorded_end_ms:120000},
  {kind:'hold',start_ms:6400,end_ms:7400,recorded_start_ms:120000,recorded_end_ms:120000}
];
const at = ms => {
  reviewPosition = ms;
  position = recordedAt(ms);
  return storyCursorRecordedAt(ms);
};
"""


def helper(name):
    """Extract a declaration without importing the browser application."""
    start = SOURCE.index("function " + name + "(")
    opening = SOURCE.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (SOURCE[end] == "{") - (SOURCE[end] == "}")
        end += 1
    return SOURCE[start:end]


class StorylineCursorMotionTests(unittest.TestCase):
    def run_js(self, script):
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def script(self, checks, axis="const storyDisplayAt = x => x, storyRecordedAt = x => x;"):
        return (SCHEDULE + axis + helper("recordedAt") + "\n"
                + helper("storyCursorRecordedAt") + "\n" + checks)

    def test_repeated_reading_holds_glide_until_next_distinct_message(self):
        self.run_js(self.script(r"""
const before = structuredClone(segments);
for (const [viewing, cursor] of [
  [0,0], [399,0], [400,0], [1400,2500], [2400,5000],
  [3400,7500], [3900,8750], [4400,10000]
]) assert.equal(at(viewing), cursor, 'visual cursor at viewing '+viewing);
for (const viewing of [400,1400,2400,3399])
  assert.equal(recordedAt(viewing), 0, 'reading holds preserve source time');
assert.equal(recordedAt(3900), 5000, 'gap evidence clock keeps its exact mapping');
assert.deepEqual(segments, before, 'cursor motion cannot rewrite source pacing');
let previous = -1;
for (let viewing=0; viewing<=7400; viewing+=10) {
  const cursor = at(viewing);
  assert.ok(cursor >= previous, 'cursor cannot jump backward at hold/gap boundaries');
  previous = cursor;
}
"""))

    def test_condensed_axis_interpolates_visible_distance_without_changing_evidence(self):
        axis = r"""
const storyDisplayAt = x => x <= 10000 ? x : x <= 110000
  ? 10000+(x-10000)/20 : 15000+x-110000;
const storyRecordedAt = x => x <= 10000 ? x : x <= 15000
  ? 10000+(x-10000)*20 : 110000+x-15000;
"""
        self.run_js(self.script(r"""
assert.equal(at(4400), 10000);
assert.equal(at(5400), 112500, 'half the viewing interval traverses half the visible distance');
assert.equal(storyDisplayAt(at(5400)), 17500);
assert.equal(recordedAt(5400), 10000, 'evidence still starts the recorded gap exactly');
assert.equal(at(6400), 120000);
""", axis))

    def test_recorded_mode_and_final_hold_keep_exact_positions(self):
        self.run_js(self.script(r"""
for (const viewing of [6400,6900,7400,8000])
  assert.equal(at(viewing), 120000, 'final reading hold stays at the final source position');
assert.equal(at(-1), -1, 'restart remains before the first source event');
smart = false;
for (const recorded of [-1,0,12.345678,10000,65000,120000]) {
  position = recorded;
  assert.equal(storyCursorRecordedAt(2400), recorded, 'Recorded mode uses exact source time');
}
"""))

    def test_seek_anchor_keeps_selected_evidence_exact_then_resumes_glide(self):
        self.run_js(self.script(r"""
storyCursorAnchor = {viewing_ms:3900, recorded_ms:5000};
assert.equal(at(3900), 5000, 'source seek lands at its exact recorded position');
assert.equal(at(4150), 7500, 'subsequent viewing progress glides from the selected position');
assert.equal(at(4400), 10000, 'cursor still reaches the next source anchor exactly');
assert.equal(at(5400), 65000, 'completed seek anchor cannot affect the next viewing interval');
assert.equal(recordedAt(5400), 10000);
"""))

    def test_direct_drag_and_untimed_sequence_do_not_invent_recorded_time(self):
        self.run_js(self.script(r"""
reviewPosition = 2400;
position = 123.456;
storyDrag = {pointerId:1};
assert.equal(storyCursorRecordedAt(), position, 'direct source scrubbing uses the pointer position');
storyDrag = null;
untimed = true;
assert.equal(storyCursorRecordedAt(), position, 'untimed sequences retain their original treatment');
"""))

    def test_all_coincident_messages_have_no_invented_travel(self):
        self.run_js(self.script(r"""
segments.splice(3);
for (const viewing of [0,400,1400,2400,3400,5000])
  assert.equal(at(viewing), 0, 'no next distinct source position means no visual movement');
"""))

    def test_near_coincident_messages_keep_cursor_visibly_moving(self):
        self.run_js(self.script(r"""
// Authored synthetic timing cluster: several distinct source milliseconds all
// occupy the same screen pixel, while each message has a full reading hold.
segments.splice(0, segments.length,
  {kind:'lead',start_ms:0,end_ms:400,recorded_start_ms:0,recorded_end_ms:0},
  {kind:'gap',start_ms:400,end_ms:1400,recorded_start_ms:0,recorded_end_ms:4000},
  {kind:'hold',start_ms:1400,end_ms:2400,recorded_start_ms:4000,recorded_end_ms:4000},
  {kind:'gap',start_ms:2400,end_ms:2480,recorded_start_ms:4000,recorded_end_ms:4001},
  {kind:'hold',start_ms:2480,end_ms:3480,recorded_start_ms:4001,recorded_end_ms:4001},
  {kind:'gap',start_ms:3480,end_ms:3560,recorded_start_ms:4001,recorded_end_ms:4003},
  {kind:'hold',start_ms:3560,end_ms:4560,recorded_start_ms:4003,recorded_end_ms:4003},
  {kind:'gap',start_ms:4560,end_ms:4640,recorded_start_ms:4003,recorded_end_ms:4005},
  {kind:'hold',start_ms:4640,end_ms:5640,recorded_start_ms:4005,recorded_end_ms:4005},
  {kind:'gap',start_ms:5640,end_ms:6440,recorded_start_ms:4005,recorded_end_ms:6500},
  {kind:'hold',start_ms:6440,end_ms:7440,recorded_start_ms:6500,recorded_end_ms:6500}
);
const before = structuredClone(segments), pixels = value => value/200;
const start = at(2000), end = at(4000);
assert.ok(pixels(end)-pixels(start) >= 2,
  'reading holds at distinct but subpixel source positions must still visibly advance');
assert.equal(recordedAt(2000), 4000);
assert.equal(recordedAt(3000), 4001);
assert.equal(recordedAt(4000), 4003);
assert.equal(recordedAt(5000), 4005);
let previous = -1;
for (let viewing=0; viewing<=7440; viewing+=10) {
  const cursor = at(viewing);
  assert.ok(cursor >= previous, 'visual clustering cannot move backward at source anchors');
  previous = cursor;
}
assert.equal(at(6440), 6500);
assert.equal(at(7000), 6500);
assert.deepEqual(segments, before, 'visual clusters cannot alter evidence clocks');
"""))


if __name__ == "__main__":
    unittest.main()
