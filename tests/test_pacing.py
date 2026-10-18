"""New synthetic smart reading schedule tests; no original recording fixtures."""
import copy
import unittest

from web.pacing import build_smart_pacing


def flow(offsets, **fields):
    return {'time_mode': 'recorded', 'messages': [
        {'id': str(i), 'offset_ms': offset, 'recorded_offset_ms': offset,
         'text': '', 'parameters_text': '', **fields}
        for i, offset in enumerate(offsets)]}


class PacingTests(unittest.TestCase):
    def test_empty_and_single_finish_after_final_reading_hold(self):
        self.assertEqual(build_smart_pacing(flow([])),
                         {'version': 1, 'duration_ms': 0, 'entries': [], 'segments': []})
        pacing = build_smart_pacing(flow([0]))
        self.assertEqual(pacing['entries'][0]['at_ms'], 400)
        self.assertEqual(pacing['duration_ms'], 1400)
        self.assertEqual(pacing['segments'][-1]['end_ms'], 1400)

    def test_equal_and_dense_offsets_reveal_sequentially(self):
        pacing = build_smart_pacing(flow([0, 0, 0.001, 1, 2]))
        entries = pacing['entries']
        for a, b in zip(entries, entries[1:]):
            self.assertGreaterEqual(b['at_ms'] - a['at_ms'], a['dwell_ms'])
        self.assertEqual([e['message_id'] for e in entries], ['0', '1', '2', '3', '4'])
        self.assertEqual([e['recorded_ms'] for e in entries], [0, 0, .001, 1, 2])

    def test_long_wait_compressed_and_mapping_is_contiguous(self):
        pacing = build_smart_pacing(flow([0, 3600000]))
        gap = next(s for s in pacing['segments'] if s['kind'] == 'gap')
        self.assertEqual(gap['end_ms'] - gap['start_ms'], 2500)
        self.assertEqual((gap['recorded_start_ms'], gap['recorded_end_ms']), (0, 3600000))
        for a, b in zip(pacing['segments'], pacing['segments'][1:]):
            self.assertEqual(a['end_ms'], b['start_ms'])
            self.assertEqual(a['recorded_end_ms'], b['recorded_start_ms'])

    def test_visible_preview_controls_dwell_and_context_does_not(self):
        short = build_smart_pacing(flow([0], text='short', context=['x' * 100000]))
        long = build_smart_pacing(flow([0], text='x' * 100000,
                                       parameters={}, parameters_text='y' * 100000))
        self.assertEqual(short['entries'][0]['dwell_ms'], 1010)
        self.assertEqual(long['entries'][0]['dwell_ms'], 4000)

    def test_parallel_evidence_and_original_offsets_are_unchanged(self):
        source = flow([0, 0, 12.345678], dependencies=['observed'], lane_id='parallel',
                      recorded_offset_ms=None, evidence_refs=['synthetic'])
        source.pop('time_mode')
        before = copy.deepcopy(source)
        pacing = build_smart_pacing(source)
        self.assertEqual(source, before)
        self.assertEqual(pacing, build_smart_pacing(source))
        self.assertEqual(pacing['entries'][-1]['recorded_ms'], 12.345678)
        self.assertIn('Review sequence', pacing['segments'][-2]['label'])

    def test_invalid_unnormalized_axis_fails_explicitly(self):
        for offsets in ([1, 0], [None], [float('nan')], [True]):
            with self.assertRaises(ValueError):
                build_smart_pacing(flow(offsets))


if __name__ == '__main__':
    unittest.main()
