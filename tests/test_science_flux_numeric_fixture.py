"""Explicit synthetic ADC + injected measured-provider fixture, never real data.

Call native_fixture() only with the pinned numerical interpreter. The envelope
is test-only; none of these receipts can pass native file/source authentication.
"""
from pathlib import Path
import json
import tempfile

def native_fixture(method='ro_flux_scan'):
    import sys
    root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'adapters/arclight_configurable_runtime'))
    import numpy as np
    from adapters.science_flux.analysis import analyze_cut
    from adapters.science_flux.plans import build_campaign
    from adapters.science_flux.bias_session import BiasSession
    from adapters.science_flux_adapter import ScienceFluxAdapter
    from adapters.science_runtime.policy import digest,sha
    from adapters.science_flux.contracts import POLICY
    from tests.test_science_flux import route,Provider,Clock
    from tests.test_science_flux_plans import parameters
    r=route();p=parameters();p['bias_values_v']=[.2]
    source=None
    if method=='sq_flux_point_recheck':
        p.pop('bias_values_v');p['local_readout_frequencies_hz']=p.pop('readout_frequencies_hz')
        p.update(drive_frequencies_hz=[4.79e9+n*1e6 for n in range(21)],drive_amp=.1,spectroscopy_width_s=2e-6)
        source={'resourceId':'Qfixture','requestedSettingV':.2,'usableReadoutBandHz':[6.98e9,7.02e9],
            'pointId':'bias-0','recordId':'fixture','routeSha256':digest(r),'workingPointId':'fixture-working-point','sha256':'f'*64}
    campaign=build_campaign(method,p,r,point=source);results=[]
    with tempfile.TemporaryDirectory(prefix='flux-numeric-only-') as temporary:
        for operation_index,stage in enumerate(('selection','verification')):
            folder=Path(temporary)/stage;folder.mkdir();provider=Provider();clock=Clock()
            def setter(value,*,timeout_seconds):provider.setting=value;return {'fixtureOnly':'recording setter'}
            session=BiasSession(r,provider,setter,folder/'bias_journal.json',identity={'operationId':stage,'resourceId':'Qfixture'},
                check_cancel=lambda:None,monotonic=clock.monotonic,sleep=clock.sleep)
            initial=session.open_and_read_initial();points=[];rng=np.random.default_rng(400+operation_index)
            for cut in campaign['cuts']:
                requested=.1 if cut['requestedSettingV'] is None else cut['requestedSettingV'];session.ramp_to(requested,cut['pointId'])
                rows=[campaign['rows'][n] for n in cut['rows']]
                i=[];q=[]
                for row in rows:
                    response=1-.3*np.exp(-.5*((row['frequencyHz']-7e9)/2e6)**2)
                    if cut['phase']=='f01':response=.2*np.exp(-.5*((row['frequencyHz']-4.8e9)/2e6)**2) if row['condition']=='on' else 0.
                    i.append(response+rng.normal(0,.0002,(1,p['shots'])));q.append(rng.normal(0,.0002,(1,p['shots'])))
                i=np.stack(i);q=np.stack(q);blocks=np.zeros_like(i,dtype=int)
                analysis=analyze_cut(cut,rows,i,q,campaign['parameters'],blocks)
                raw=folder/f'cut_{cut["cutId"]:03d}.npz';np.savez_compressed(raw,i=i,q=q,acquisition_block_index=blocks)
                point={'cutId':cut['cutId'],'pointId':cut['pointId'],'phase':cut['phase'],'requestedSettingV':requested,
                    'biasReceipt':session.verify_held(cut['pointId']),'analysis':analysis,'frequenciesHz':cut['frequenciesHz'],
                    'responseMagnitude':analysis['responseMagnitude'],'readoutFrequencyHz':7e9 if cut['phase']=='f01' else analysis['candidateHz'],
                    'f01Hz':analysis['candidateHz'] if cut['phase']=='f01' else None,'rawSha256':sha(raw),'rawPath':str(raw),'rowIds':cut['rows']}
                point['cutSha256']=digest(point);points.append(point)
            restoration=session.restore_initial('fixture_completed')
            completion={'state':'completed','testOnly':False,'shutdown_ok':True,'policy':POLICY,'operationId':stage,
                'resourceId':'Qfixture','routing':{},'points':points,'initial':initial,'restoration':restoration,
                'rfShutdown':{'verified':True,'physicalOutputReadback':False,'commands':{'fixture':{'ok':True}}},
                'transportClose':{'shutdown_ok':True},'providerClosed':True,'acquiredAt':initial['observedAt'],
                'analysis':{'analysis':{'accepted':all(pt['analysis']['accepted'] for pt in points),'reasons':[]},
                    'metrics':{'resolved_bias_points':1 if method=='ro_flux_scan' else 2,'acquired_bias_points':1 if method=='ro_flux_scan' else 2,
                        'scan_coverage':1,'bias_readback_coverage':1,'restoration_verified':1,
                        **({'local_cavity_contrast_snr':points[0]['analysis']['contrast_snr'],'peak_snr':points[1]['analysis']['contrast_snr']} if method=='sq_flux_point_recheck' else {})},
                    'scopedCandidateParameters':{'readout_hz':7e9,'drive_hz':4.8e9} if method=='sq_flux_point_recheck' else {},'series':[]}}
            np.savez_compressed(folder/'raw_scan.npz',i=i,q=q)
            completion['files']=[str(path) for path in folder.iterdir()]
            plan={**campaign,'operationId':stage,'resourceId':'Qfixture','procedureId':method,'routing':{},'policy':POLICY,
                'biasRoute':r,'profileHash':'0'*64,'sourceHash':'1'*64,'validationEvidence':{'closureSha256':'2'*64}}
            adapter=ScienceFluxAdapter(root,{'id':'fixture','resources':{'Qfixture':{}},'hardwareLocks':[]})
            result=adapter._result(folder,plan,{'parameterVersions':{}},completion)
            results.append(result)
    return {'fixtureOnly':True,'neverAcquiredFromHardware':True,'description':'Actual analyze_cut on synthetic ADC; real BiasSession on injected setting/meter; result-shaped contract fixture only',
            'plan':plan,'selectionResult':results[0],'verificationResult':results[1]}
