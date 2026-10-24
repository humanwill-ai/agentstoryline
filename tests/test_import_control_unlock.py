"""Synthetic check that completed imports unlock the telemetry workflow."""
import json
from pathlib import Path
import subprocess
import unittest

class ImportControlsTests(unittest.TestCase):
    def test_import_lock_unlock_includes_telemetry_controls(self):
        source=(Path(__file__).resolve().parents[1]/'web/flow.js').read_text()
        start=source.index('function setImportChooserDisabled(')
        helper=source[start:source.index('\nfunction ',start+1)]
        script='''const ids=['open-recording','import-recording','export-recording','attach-telemetry','telemetry-view'];const controls=Object.fromEntries(ids.map(id=>[id,{disabled:false}]));const document={getElementById:id=>controls[id],querySelectorAll:()=>[]};let importInFlight=true;const hasLoadedRecording=()=>true;'''+helper+'''
setImportChooserDisabled(false);const during=ids.map(id=>controls[id].disabled);importInFlight=false;setImportChooserDisabled(false);const after=ids.map(id=>controls[id].disabled);setImportChooserDisabled(true);const busy=ids.map(id=>controls[id].disabled);process.stdout.write(JSON.stringify({during,after,busy}));'''
        result=json.loads(subprocess.check_output(['node','-e',script],text=True))
        self.assertEqual(result,{'during':[True]*5,'after':[False]*5,'busy':[True]*5})
