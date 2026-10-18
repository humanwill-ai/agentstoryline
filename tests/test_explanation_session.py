"""Synthetic persistent session jobs; never invokes a provider."""
from analysis.explanation_handoff import (HandoffError, offline_start, offline_status,
    offline_pending, offline_cancel)
from tests import test_explanation_handoff as handoff_tests


import unittest


class SessionTests(unittest.TestCase):
    setUp = handoff_tests.HandoffTests.setUp
    reopen = handoff_tests.HandoffTests.reopen
    export = handoff_tests.HandoffTests.export
    response = handoff_tests.HandoffTests.response
    apply = handoff_tests.HandoffTests.apply
    def start_job(self):
        return offline_start(self.service, {'recording_id': self.service.recording_id, 'scope': 'all'})

    def test_queue_restart_partial_and_idempotent(self):
        first = self.start_job()
        self.assertEqual((first['state'], first['completed'], first['total']), ('waiting', 0, 45))
        self.assertEqual(self.start_job(), first)
        request = offline_pending(self.service)
        self.apply(self.response(request, request['requests'][:1]))
        self.assertEqual(offline_status(self.service)['completed'], 20)
        self.service = self.reopen()
        self.assertEqual(offline_status(self.service)['completed'], 20)
        self.apply(self.response(request, request['requests'][:1]))
        self.assertEqual(offline_status(self.service)['completed'], 20)
        self.apply(self.response(request, request['requests'][1:]))
        self.assertEqual(offline_status(self.service)['state'], 'complete')
        self.assertIsNone(offline_pending(self.service))

    def test_cancel_rejects_stale_worker_and_new_job(self):
        self.start_job()
        request = offline_pending(self.service)
        config = {'recording_id': self.service.recording_id}
        self.assertEqual(offline_cancel(self.service, config)['state'], 'cancelled')
        with self.assertRaises(HandoffError): self.apply(self.response(request))
        replacement = self.start_job()
        self.assertNotEqual(replacement['request_id'], request['request_id'])
        with self.assertRaises(HandoffError): self.apply(self.response(request))
        self.assertNotIn('explanations', self.service.package['payload'])

    def test_queue_progress_counts_imports_not_annotations(self):
        manual = self.export()
        self.apply(self.response(manual))
        self.start_job()
        self.assertEqual(offline_status(self.service)['completed'], 0)
        self.apply(self.response(manual))
        self.assertEqual(offline_status(self.service)['completed'], 0)

    def test_busy_stale_model_and_api_guards(self):
        self.service.status['state'] = 'running'
        with self.assertRaises(HandoffError): self.start_job()
        self.service.status['state'] = 'idle'
        with self.assertRaises(HandoffError): offline_start(self.service, {'recording_id': 'stale'})
        with self.assertRaises(HandoffError): offline_start(self.service, {'recording_id': self.service.recording_id, 'model': 'other'})
        self.start_job()
        with self.assertRaises(ValueError): self.service.start({})

    def test_corrupt_state_is_safe(self):
        self.start_job()
        path = self.service.state_dir / self.service.recording_id / 'offline-job.json'
        path.write_text('secret malformed')
        status = offline_status(self.service)
        self.assertEqual(status['state'], 'failed')
        self.assertNotIn('secret', status['error'])
        self.assertIsNone(offline_pending(self.service))

    def test_progress_failure_reports_saved_descriptions_and_retry_recovers(self):
        from unittest.mock import patch
        self.start_job()
        request = offline_pending(self.service)
        response = self.response(request, request['requests'][:1])
        with patch('analysis.explanation_handoff._save_job', side_effect=OSError('synthetic failure')):
            with self.assertRaisesRegex(HandoffError, 'Descriptions saved, but offline job progress'):
                self.apply(response)
        self.assertEqual(len(self.service.package['payload']['explanations']['annotations']), 20)
        self.assertEqual(offline_status(self.service)['completed'], 0)
        history = self.service.store.history(self.service.bundle)
        self.apply(response)
        self.assertEqual(offline_status(self.service)['completed'], 20)
        self.assertEqual(history, self.service.store.history(self.service.bundle))
