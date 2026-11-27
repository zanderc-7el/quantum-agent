"""Versioned evidence only: temporary files/DB, never acquisition or devices."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest

from backend.common import ROOT
from backend import prior_failure_context as audit

V3 = 'arclight-private-f8-q11-v3'


def install_sources(target):
    names = [row[0] for row in audit.PROFILE_FILES.values()] + [
        'adapters/arclight_runtime/source_manifest.json',
        'adapters/arclight_runtime/platform_analysis.py',
        'adapters/arclight_runtime/analyze_single_qubit.py',
        'adapters/arclight_profile_v3.json',
        'adapters/arclight_analysis_v3/source_manifest.json',
        'adapters/arclight_analysis_v3/execute_worker.py',
        'adapters/arclight_analysis_v3/platform_analysis.py',
    ]
    for name in names:
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / name).read_bytes())


class VersionedEvidenceContracts(unittest.TestCase):
    def test_exact_version_binding_preserves_legacy_and_uses_new_analysis(self):
        self.assertTrue(callable(getattr(audit, 'load_audited_bindings', None)))
        old = audit.audited_bindings(audit.IMPLEMENTATION,
            {key:(ROOT/name).read_bytes() for key,(name,_) in audit.PROFILE_FILES.items()},
            (ROOT/'adapters/arclight_runtime/source_manifest.json').read_bytes())
        bound = audit.load_audited_bindings(ROOT, audit.IMPLEMENTATION)
        for key, value in old.items():
            self.assertEqual(bound[key], value)
        new = bound[V3]
        self.assertEqual(new['analysisSourcePath'], 'adapters/arclight_analysis_v3/platform_analysis.py')
        self.assertEqual(new['manifest']['platform_analysis.py'], hashlib.sha256((ROOT/new['analysisSourcePath']).read_bytes()).hexdigest())
        self.assertNotEqual(new['manifest']['platform_analysis.py'], old['arclight-private-f8-q11-v2']['manifest']['platform_analysis.py'])
        self.assertEqual(new['profileFileSha256'], hashlib.sha256((ROOT/'adapters/arclight_profile_v3.json').read_bytes()).hexdigest())
        self.assertEqual(len(new['sourceFiles']), 108)
        self.assertEqual(audit.load_audited_bindings(ROOT, 'custom.ArcLightAdapter'), {})

    def test_changed_v3_profile_manifest_runner_or_analysis_never_upgrades_to_v2(self):
        self.assertTrue(callable(getattr(audit, 'load_audited_bindings', None)))
        for name in ('adapters/arclight_profile_v3.json', 'adapters/arclight_analysis_v3/source_manifest.json',
                     'adapters/arclight_analysis_v3/execute_worker.py', 'adapters/arclight_analysis_v3/platform_analysis.py'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); install_sources(root)
                self.assertIn(V3, audit.load_audited_bindings(root, audit.IMPLEMENTATION))
                path = root/name; path.write_bytes(path.read_bytes()+b'\n')
                bound = audit.load_audited_bindings(root, audit.IMPLEMENTATION)
                self.assertNotIn(V3, bound)
                self.assertIn('arclight-private-f8-q11-v2', bound)

    def test_v3_failure_projection_keeps_its_own_source_and_rejects_old_analysis(self):
        self.assertTrue(callable(getattr(audit, 'load_audited_bindings', None)))
        from tests.test_prior_failure_context import PriorFailureContextTests
        fixture = PriorFailureContextTests(); fixture.setUp()
        try:
            bindings = audit.load_audited_bindings(ROOT, audit.IMPLEMENTATION)
            fixture.bindings['arclight-private-f8-q11-v1'] = bindings[V3]
            record = fixture.fixture(independent=True)
            record['workflow']['adapterId'] = V3
            for effect in record['effects']:
                effect['request']['adapterId'] = V3
            current = {**fixture.current, 'adapterId':V3, 'adapterProfileHash':bindings[V3]['fingerprint']}
            value = audit.project_failure_case(record, current, fixture.node, bindings)
            self.assertIsNotNone(value)
            self.assertEqual(value['sourceManifestSha256'], bindings[V3]['sourceManifestSha256'])
            self.assertEqual(value['sourceAdapter'], V3)
            damaged = deepcopy(record)
            damaged['effects'][0]['result']['analysisQuality']['source_sha256'] = bindings['arclight-private-f8-q11-v2']['manifest']['platform_analysis.py']
            self.assertIsNone(audit.project_failure_case(damaged, current, fixture.node, bindings))
        finally:
            fixture.tearDown()

    def test_v3_capture_diagnostics_use_new_analysis_with_actual_numerical_reader(self):
        from tests.test_capture_diagnostics import CaptureDiagnosticsTests
        from backend.capture_diagnostics import calculate_diagnostics
        fixture = CaptureDiagnosticsTests(); fixture.setUpClass()
        try:
            install_sources(fixture.root)
            fixture.bindings = audit.load_audited_bindings(fixture.root, audit.IMPLEMENTATION)
            fixture.fixture(independent=True, identity=V3)
            resolved = fixture.resolve()
            self.assertEqual(resolved['status'], 'resolved', resolved)
            self.assertEqual(resolved['sourceBinding']['adapterId'], V3)
            result = calculate_diagnostics(resolved)
            self.assertEqual(result['status'], 'available', result)
            self.assertEqual(len(result['operations']), 2)
            damaged = deepcopy(resolved)
            damaged['sourceBinding']['adapterId'] = 'arclight-private-f8-q11-v2'
            self.assertEqual(calculate_diagnostics(damaged)['status'], 'unavailable')
            damaged = deepcopy(resolved); damaged.pop('sourceBinding')
            self.assertEqual(calculate_diagnostics(damaged)['status'], 'unavailable')
            path = fixture.root / audit.V3_ANALYSIS
            path.write_bytes(path.read_bytes() + b'\n# TEST_ONLY changed after resolution\n')
            self.assertEqual(calculate_diagnostics(resolved)['status'], 'unavailable')
        finally:
            fixture.tearDownClass()

    def test_v3_prior_failure_normal_controller_entry_never_writes_and_rechecks_raw(self):
        from types import SimpleNamespace
        from tests.test_prior_failure_context import PriorFailureContextTests
        fixture = PriorFailureContextTests(); fixture.setUp()
        try:
            bindings = audit.load_audited_bindings(ROOT, audit.IMPLEMENTATION)
            fixture.bindings['arclight-private-f8-q11-v1'] = bindings[V3]
            record = fixture.fixture(independent=True)
            record['workflow']['adapterId'] = V3
            for effect in record['effects']:
                effect['request']['adapterId'] = V3
            current = {**fixture.current, 'adapterId':V3, 'adapterProfileHash':bindings[V3]['fingerprint']}
            controller, db = fixture.database(record)
            try:
                install_sources(controller.root)
                before = db.total_changes
                out = controller._prior_failure_context(SimpleNamespace(db=db), current, fixture.node)
                self.assertEqual(len(out['cases']), 1, out)
                self.assertEqual(out['cases'][0]['sourceAdapter'], V3)
                raw = next(a for a in record['effects'][-1]['result']['artifacts'] if a['kind'] == 'raw')
                Path(raw['sourcePath']).write_bytes(b'TEST_ONLY changed raw')
                self.assertEqual(controller._prior_failure_context(SimpleNamespace(db=db), current, fixture.node)['cases'], [])
                self.assertEqual(db.total_changes, before)
            finally:
                db.close()
        finally:
            fixture.tearDown()

    def test_v3_accepted_upstream_context_keeps_stage_and_current_evidence_separate(self):
        from tests.test_backtrack_upstream import AcceptedUpstreamTests
        fixture = AcceptedUpstreamTests(); fixture.setUpClass()
        try:
            install_sources(fixture.root)
            fixture.bindings = audit.load_audited_bindings(fixture.root, audit.IMPLEMENTATION)
            fixture.profile_identity = V3
            fixture.test_authenticated_pair_keeps_stage_metrics_and_does_not_expand_current_evidence()
            fixture.test_stale_partial_latest_and_native_failed_never_become_reference()
        finally:
            fixture.tearDownClass()

    def test_v3_withdrawal_normal_controller_path_preserves_archive_and_rejects_changed_raw(self):
        from tests.test_readout_withdrawal import ReadoutWithdrawalTests
        for method in ('test_success_preserves_history_budgets_and_fixed_baseline',
                       'test_changed_raw_registry_and_cleanup_fail_closed'):
            with self.subTest(method=method):
                fixture = ReadoutWithdrawalTests(); fixture.profile_version = 3; fixture.setUp()
                try:
                    getattr(fixture, method)()
                finally:
                    fixture.tearDown()


if __name__ == '__main__':
    unittest.main()
