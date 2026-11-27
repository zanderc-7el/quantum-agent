# V3 native acquisition implementation, disabled by default

The six implemented procedures are shape Rabi amplitude, independent half
rotation, DRAG diagnostic scan, conditional f12 spectrum, conditional pi12 Rabi,
and held-out SHELVE on/off logical-assignment comparison. `build_science_profile`
always starts disabled. The existing controller owns leases, attempts, effects,
manual acceptance and stopping; this adapter does not create another dispatcher.

`ScienceV3Adapter(root, profile)` exposes parameter_schema/defaults, build_plan,
build_verification_parameters, authenticate_dependency and inherited execute /
reconcile. Requests bind run/node/attempt/operation/resource, lease token,
parameter versions, explicitParameterKeys and the complete server-resolved
scienceDependencies closure. Only same published chip/deployment/physical route
or a frozen observed-resource lineage can seed another workflow. Observed Q11
does not assert a known physical design identity.

The worker verifies source closure and receipt identities, compiles and archives
all bounded rows before opening instruments, then recompiles each row and checks
every native waveform byte hash immediately before the existing driver's upload.
It uses the existing native SequenceRunner, repeated fresh ADC block acquisition,
delegated shared locks and close_resources. Upload commands have completion
receipts; they are not device waveform readback. Cleanup failure retains locks
and requires reconciliation. No LF/DC set command is introduced.

Every verification re-acquires the frozen selected candidate plus an independent
scan. Both mean and each capture must agree with the independent prediction.
DRAG requires resolved positive curvature and an interior optimum; its covariance
uncertainty is a fit diagnostic, not a calibrated confidence interval. No DRAG
scan reports gate fidelity. The SHELVE comparison uses separate training and
held-out captures for both conditions, with conditional ADC response SNR >= 5
and ground-disturbance ratio <= .2. It reports binary logical assignment only,
not |2> population, transfer fidelity, leakage or three-state selectivity.

On 2026-11-26 the explicit first-batch plus six-method/controller suite passed
79 tests in 21.414 seconds. The nested native suite contains seven tests and
compiles 28 distinct first-capture circuits spanning all six methods. Tests use
the configured Python312 and actual native compiler/runner/acquisition/cleanup;
only hardware objects are injected. Network and subprocess creation in that
native test child are blocked. No station was connected and no physical
calibration outcome is asserted.

IRB is deliberately unavailable in this release. Its separate third-batch
implementation must bind the same actual DRAG X/2 pulse, native paired seeds and
recovery, fresh held-out PRE and POST drift evidence before integration. LF
methods remain unavailable without an explicit route/readback/restoration policy.

`source_manifest.json` freezes production Python sources; `acceptance.json`
records test and source hashes. The original offline compiler release and its
bridge-only manifest migration remain separate.

The subsequent bootstrap-entry regression exposed an empty-directory conflict:
native bootstrap creates `results` before this worker starts. The worker now
accepts that empty directory and refuses any existing contents. Native plus
controller/HTTP regression passed 17 tests after this fix; the updated manifest
is 6eaa77f2365dc5064607cccd87e5c2b556adce7f0089d0fd0da9b86f4cd013e1.
