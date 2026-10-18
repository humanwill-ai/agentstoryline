"""Source dispatch must use declared adapters rather than display labels."""
import copy
import unittest
from tests.test_explanation_workflow import synthetic_bundle
from web.render import build_payload


class ViewerIdentityTests(unittest.TestCase):
    def test_explicit_artex_survives_display_rename(self):
        bundle = synthetic_bundle()
        bundle['manifest']['source'] = {'adapter': 'artex'}
        bundle['manifest']['agent']['name'] = 'Customer display name'
        payload = build_payload(bundle, {})
        self.assertEqual(payload['flow']['title'], 'Artex recorded playback')
        self.assertEqual(payload['bundle'], bundle)

    def test_unknown_explicit_adapter_does_not_use_artex_semantics(self):
        bundle = synthetic_bundle()
        bundle['manifest']['agent']['name'] = 'Artex'
        bundle['manifest']['source'] = {'adapter': 'future-agent'}
        original = copy.deepcopy(bundle)
        payload = build_payload(bundle, {})
        self.assertNotEqual(payload['flow']['title'], 'Artex recorded playback')
        self.assertEqual(bundle, original)
