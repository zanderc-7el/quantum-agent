"""Controller admission and recovery using real ArcLight plans and test-only transport."""
import unittest
from copy import deepcopy

from backend.common import APIError
from tests import test_gate_calibration_context as fixtures


class SpectrumDrivePreflight(unittest.TestCase):
    def setUp(self):
        self.h = fixtures.GateContextContracts()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.c = self.h.c

    def spectrum(self, wf):
        return next(n for n in wf['nodes'] if n['procedureId']=='sq_spectroscopy')

    def request(self, wf, op, parameters, **extra):
        return {'operationId':op,'expectedRevision':wf['revision'],'parameters':parameters,**extra}

    def set_spectrum(self, wf, changes, name):
        return self.c.set_parameters(wf['id'], self.spectrum(wf)['id'], self.request(wf,name,changes))

    def counts(self, wf):
        with self.c.store.connect() as rt:
            return tuple(rt.db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in
                ('platform_commands','platform_effects','platform_attempts','platform_leases','parameters'))+(wf['reservedShots'],len(self.h.calls))

    def test_initial_high_power_spectrum_has_no_committed_pi_dependency(self):
        wf=self.h.create()
        cavity=next(n for n in wf['nodes'] if n['procedureId']=='ro_resonator_survey')
        self.h.command(wf,'run_step',nodeId=cavity['id']);self.c.run_workflow(wf['id'])
        wf=self.h.command(self.c.get(wf['id']),'approve',nodeId=cavity['id'])
        node=self.spectrum(wf)
        wf=self.set_spectrum(wf,{'drive_amp':.98,'points':21},'initial-high-amp')
        self.assertEqual(self.c.detail(wf['id'],node['id'])['resolvedExecutionPlan']['status'],'ready')
        self.c.pulse_preview(wf['id'],node['id'],{'expectedRevision':wf['revision'],'parameters':{}})
        self.h.command(wf,'run_step',nodeId=node['id'])

    def test_saved_override_blocks_readonly_preview_manual_and_dispatch_and_can_be_cleared(self):
        wf=self.h.accepted_rabi()
        node=self.spectrum(wf);node_id=node['id']
        wf=self.h.command(wf,'backtrack',targetNodeId=node_id)
        wf=self.set_spectrum(wf,{'drive_amp':.98,'points':21},'old-scan-override')
        before=self.counts(wf); detail=self.c.detail(wf['id'],node_id);plan=detail['resolvedExecutionPlan']
        self.assertEqual(plan['code'],'SPECTRUM_DRIVE_CONTEXT_CONFLICT')
        for term in ('drive_amp','half_pi_amp','.865521','.98'):
            self.assertIn(term,plan['reason'])
        for check in (lambda:self.c.pulse_preview(wf['id'],node_id,{'expectedRevision':wf['revision'],'parameters':{}}),
                      lambda:self.h.command(wf,'run_step',nodeId=node_id)):
            with self.assertRaises(APIError) as err:check()
            self.assertEqual(err.exception.code,plan['code'])
            self.assertEqual(str(err.exception),plan['reason'])
        self.assertEqual(self.counts(self.c.get(wf['id'])),before)
        self.assertEqual(self.c.get(wf['id'])['revision'],wf['revision'])
        # A corrected, unsaved draft is previewed without mutating the saved conflict.
        value=.8655218444566908
        self.c.pulse_preview(wf['id'],node_id,{'expectedRevision':wf['revision'],'parameters':{'drive_amp':value}})
        self.assertEqual(self.c.detail(wf['id'],node_id)['resolvedExecutionPlan']['code'],plan['code'])
        versions=deepcopy(detail['parameterVersions'])
        raw=self.request(wf,'clear-scope',{},clearSpectrumDriveOverrides=True)
        restored=self.c.set_parameters(wf['id'],node_id,raw)
        self.assertEqual(self.c.set_parameters(wf['id'],node_id,raw)['revision'],restored['revision'])
        after=self.c.detail(wf['id'],node_id)
        self.assertEqual(after['node']['parameterOverrides'],{'points':21})
        self.assertEqual(after['parameterVersions'],versions)
        self.assertEqual(after['resolvedExecutionPlan']['parameters']['drive_amp'],value)
        self.assertEqual(after['resolvedExecutionPlan']['parameters']['half_pi_amp'],value)
        self.assertEqual(len(self.h.calls),before[-1])
        with self.assertRaises(APIError):
            self.c.set_parameters(wf['id'],node_id,self.request(wf,'obsolete-clear',{},clearSpectrumDriveOverrides=True))
        restored=self.h.command(restored,'run_step',nodeId=node_id)
        self.c.run_workflow(wf['id'])
        self.assertEqual(self.h.calls[-1]['parameters']['drive_amp'],value)

    def test_rechecks_queued_permission_before_the_worker_acquires_a_lease(self):
        wf = self.h.accepted_rabi()
        node_id = self.spectrum(wf)['id']
        wf = self.h.command(wf, 'backtrack', targetNodeId=node_id)
        wf = self.h.command(wf, 'run_step', nodeId=node_id)
        # Recreate a queued pre-upgrade permit, bypassing the public admission
        # path only to seed the old state inside this disposable fixture.
        with self.c.store.transaction() as rt:
            persisted = self.c.get(wf['id'], rt)
            self.spectrum(persisted).setdefault('parameterOverrides', {}).update(drive_amp=.98, half_pi_amp=.98)
            self.c.store.save(rt, persisted, 'TEST_ONLY old permission')
        before = self.counts(persisted)
        self.assertFalse(self.c._execute_node(wf['id'], node_id))
        after = self.c.get(wf['id'])
        self.assertEqual(self.spectrum(after)['lastAdmissionError']['code'], 'SPECTRUM_DRIVE_CONTEXT_CONFLICT')
        self.assertEqual(self.spectrum(after)['attempt'], self.spectrum(persisted)['attempt'])
        self.assertEqual(self.counts(after), before)

