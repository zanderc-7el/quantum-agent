# Science v3 executable integration review

Status: **implementation design, not enabled or delivered live capability**.
2026-11-26. First-batch `science_runtime` is frozen independently. No connection,
driver instantiation, calibration acquisition, station command or live deployment
has been performed for this design.

## File ownership and activation boundary

New files owned by native_compile_1125 in the next implementation batch:

- `adapters/science_v3_adapter.py`: `ScienceV3Adapter(DynamicArcLightAdapter)`.
- `adapters/science_v3/profile.py`: detached profile constructor and scalar bounds.
- `adapters/science_v3/plans.py`: bounded method schemas, numeric scan expansion,
  native spec construction, dependency/evidence admission and budget estimates.
- `adapters/science_v3/compiler.py`: per-instance native compiler/sequence extension,
  full-resolution waveform archive and upload identity guard.
- `adapters/science_v3/execute_worker.py`: one process for one controller operation;
  source/plan/lease checks, shared chassis locks, bounded job loop, partial results.
- `adapters/science_v3/transport.py`: bounded native RF/ADC transport, actual block
  acquisition, raw-array checks, upload receipts, cooperative stop, native cleanup.
- `adapters/science_v3/analysis.py`: shape-Rabi, DRAG diagnostic, transition spectra,
  conditional pi12 Rabi and logical readout assignment analysis.
- `adapters/science_v3/evidence.py`: immutable pulse identities, current accepted
  acquisition/verification references, same-resource/configuration checks.
- `adapters/science_v3/source_manifest.json` and `tests/test_science_v3*.py`.

Root-owned integration, reviewed after executable adapter tests pass:
`backend/controller.py` adapter selection/binding and source validation;
`backend/catalog.py` method nodes, dependencies and manual method descriptions;
`backend/quality.py` explicit gates; `backend/experiment_setup.py` scalar/form
schemas and selected method admission; `backend/workspace.py` profile policy
selection if needed; frontend forms/actions. Existing `arclight.py`, both frozen
runtime trees and both old profiles stay unchanged. V3 files are excluded from
the first deployment manifest.

No new HTTP endpoint may execute a campaign directly. Science preview can build
and review a plan; every measurement is a normal workflow node action.

## Existing execution seam (verified source)

`Controller._execute_node` already validates the effective plan **before** it
inserts `platform_leases`, `platform_effects`, or an attempt. It supplies runId,
nodeId, attemptId, operationId, resourceId, parameterVersions, leaseToken,
validationSnapshot and an execution deadline. Keep this as the only entry.

Inherit `ArcLightAdapter.execute`, `reconcile`, hardware ownership and scoped
`with_execution_enabled`, preserving their operation-id idempotency, intent,
STOP_AFTER_POINT, event pump and cancellation handling. Override `build_plan`,
`_result`, `capabilities`, `parameter_schema`, `validation_parameter_keys`,
`normalize_candidate`, `build_verification_parameters`, `preflight` and calibration
publication. `_result` cannot call the old method-label table for new method IDs.

Old `DynamicArcLightAdapter.preflight` hardcodes its old runtime coverage and
must not be used unchanged for v3. The v3 preflight verifies both frozen native
manifests plus its own allowlisted source closure, profile, interpreter, route,
and shared locks without importing drivers. The detached profile starts
`enabled:false`, with its own runner/hash/policy and the existing physical device
identity and chassis lock names. Do not invent a second lock group for v3.

Inside the new worker, reuse the verified native `platform_worker.acquire_locks`
protocol and `platform_hooks.check_cancel`/`DelegatedLock` semantics. The worker
must compare the received profile/source/route hash and controller request
identity with the frozen plan before import/open; reject preview policy and any
`compileOnly`, `compiled` or `testOnly` admission artifact as execution evidence.
No endpoint or client may supply an accepted calibration flag or trusted receipt.

## Real RF/ADC transport

The native `run_single_qubit.py` provides the established exact sequence:
create CeyearStationF8 with explicit address and VISA library; assign RO/AD/XY
logical channels from the validated route; acquire delegated lock; `hardware.open`;
read `*IDN?`; select/read actual ADC clock; configure readout sample rate, ADC
shots/delay, XY filter with readback, XY attenuation and hardware XY delay.
The v3 transport factors this behavior into its own bounded implementation using
the unchanged native driver. It does not invoke the old main script as a generic
v3 job runner, because that entry installs the old short-half policy.

`core.experiment.runner.SequenceRunner.get_raw_data` is the actual upload → arm →
finite trigger → ADC-read implementation. Use its normal `seq` interface.
`get_repeated_raw_data` reuses the first native transaction, then performs fresh
uncached arm/finite trigger/read for subsequent blocks and saves each block.
Preserve its shape/finite-number checks and exact-duplicate detection, and the
outer entry's independent shape checks for final arrays.

Before any instrument open, compile **every** requested sequence variant and
archive full XY and readout arrays (deduplicate identical readout arrays by hash).
Validate physical route, sample rate, native grid, peak <=0.99, no Z/DC writes,
start/end bounds, total shots, total rows, deadline and archive-byte budget.

The native runner regenerates samples before upload. A per-operation guarded
sequence/driver wrapper must compare each regenerated `w.get_data()` dtype,
shape, sample rate and canonical array SHA256 against that row's frozen archive
immediately before `set_wave`. Record the actual successful `set_wave` return
as `upload_commands_completed`, not device waveform readback. A receipt binds
row id, operation/attempt, resource/route, pulse identity, array hash and ADC
block hashes. An upload exception has no successful upload receipt.

All failures and stops enter native `close_resources(hardware, experiment)`.
This tries each independent RF/ADC cleanup and close, reports unresolved used
channels and remaining handle, and removes the native instance only after
successful cleanup. `shutdown_ok=false`, missing completion or forced process
termination must retain shared locks and controller quarantine. Partial rows,
raw blocks and upload receipts survive. A confirmed stop never automatically
replays the same operation. Cancels are checked before each point and between
finite ADC blocks; the configured driver call timeout bounds the in-flight block.

## Native compiler installation, separate from the frozen v2 policy

Policy name: `native_science_v3_fixed_duration_drag_segmented_carrier_v1`.
Fixed pulse duration, 2 ns total zero buffer; half rotation scales amplitude.
All gate signs, native virtual-Z and decomposition conventions remain native.
Gaussian sigma is explicit seconds; CosPulse uses the native function;
beta = -lambda1/(2*pi*Delta2), with signed Delta2 in Hz. No implicit -200 MHz
placeholder is accepted as physical DRAG evidence.

The verified native Experiment has an instance method
`_build_sequence_from_gates(setting_config, gates)`. Override/bind **that instance
method** to construct V3Compiler and V3SegmentedSequence, retaining native
`_prepare_sequence_for_axes`, scan overrides, gate parsing and RB synthesis.
Do not monkeypatch global compiler classes, change a native registry entry, or
call `install_validated_short_half` for v3. Per-gate fields travel through actual
`exp.cir.<variant>.<gate-id>.pulse.<field>` gate_param axes (singleton for constants).
The new compiler merges validated per-gate data with per-qubit pulse identities.
For SHELVE, prep01 and probe12 use independent pulse identities and frequencies;
changing a probe axis never changes prep01, readout, or ADC.

Reuse first-batch native segment mixing behavior, but recompile under the v3
execution policy and stricter <=0.99 peak bound. Never relabel or execute a
compile-only receipt. Assert same-frequency equivalence against unchanged
native Wave.mix, sign/virtual-Z and arbitrary multi-gate RB recovery sequences.

## Executable methods and prerequisite graph

These prerequisites are work to acquire, not permanent reasons to omit the
feature. Existing accepted drive/readout acquisition can seed the first node;
shape-specific evidence is generated by the new nodes below.

| Method ID | Fixed quantities / scan | New acquired output | Required upstream |
|---|---|---|---|
| `sq_shape_rabi_amplitude` | fixed f01, envelope shape/length/sigma, lambda1=0; amplitude axis | pi01 candidate for this exact shape; raw IQ/Rabi fit | accepted f01 and usable readout; old pulse only seeds |
| `sq_shape_rabi_verify` | frozen candidate plus predeclared neighboring/fixed control points; separate operation | independent amplitude/rotation diagnostic | selected shape-Rabi attempt, exact candidate hash |
| `sq_shape_half_verify` | fixed-duration half-amplitude RX(pi/2) and complementary analysis sequences | independently checked half-gate convention | accepted pi01 shape identity |
| `sq_drag_scan` | fixed target rotation/shape/length/phase/f01/Delta2, lambda1 axis; specified amplification/analysis sequence | diagnostic error vs lambda1; candidate lambda1 | current accepted shape pi/half and readout; explicit Delta2 source |
| `sq_drag_verify` | candidate lambda1 and prespecified baseline/control; fresh acquisition | independently repeated diagnostic optimum | selected scan attempt and candidate hash |
| `sq_drag_irb` | fresh PRE, paired reference/IRB seeds, POST; same immutable pulse set | conditional gate fidelity and uncertainty, or nonreportable reasons | accepted candidate pulse plus shape-specific Clifford primitive validation |
| `sq_shelve_spectrum12` | fixed independently accepted X01; probe12 frequency axis; prep-off and zero-probe controls | resonance candidate with prep dependence; raw IQ | accepted pi01/readout; configured safe probe range/passband |
| `sq_shelve_frequency_verify` | frozen candidate f12 and prespecified neighboring controls | independently rechecked conditional resonance | selected spectrum attempt; f02 peak identity remains explicit |
| `sq_shelve_rabi12` | fixed X01 and accepted f12; fixed probe shape/length; probe amplitude axis | pi12 candidate from actual conditional Rabi | accepted conditional f12 |
| `sq_shelve_pi12_verify` | candidate amplitude, fixed controls and fresh reference | independently repeated transfer diagnostic | selected pi12 candidate and exact pulse hash |
| `sq_shelve_readout_compare` | four preparations: off+logical0/1, on+logical0/1; fixed identical readout | held-out n0/n1/wrong0/wrong1/F and delta pp across captures | accepted pi01/pi12; selective-transfer protocol and readout classifier evidence |

For f12 spectroscopy, lack of a known f12 is precisely what the scan addresses.
The preliminary range may be centered on 2*twoPhotonHz-f01 only when peak
identity/source is recorded, otherwise it is explicitly a search hypothesis.
Hardware XY passband/range confirmation is a route prerequisite, not a claimed
result of numerical compilation. Lack of pi12 is resolved by the Rabi12 stage.

Readout comparison keeps original logical labels after shelving. Use independent
train/validation capture partitions, document whether the classifier is shared
or separately trained per condition, keep that protocol fixed and report held-out
assignment only. Binary IQ discrimination alone must not be presented as a
three-state leakage measurement or proof of selectivity. If the physical
selectivity diagnostic cannot resolve the hypothesis, acquisition can be saved
as a diagnostic but downstream scientific acceptance remains pending.

## Concrete parameter and result interface

Existing controller request fields remain unchanged. V3 `parameters` adds:

- `science_policy` (single allowed policy enum), `pulse_shape` (`Gaussian` or
  `CosPulse`), `pulse_length_s` (4 ns..2 us on 0.25 ns grid), `pulse_sigma_s`
  (mandatory Gaussian, 0.25 ns..length/2), `pulse_phase_rad`, `target_rotation`
  (`X` or `X/2`), `drag_lambda1` (-10..10), `anharmonicity_hz` (signed nonzero).
- `amplitudes` (0..0.99), `lambda_values`, `frequencies12_hz`, `probe_amplitudes`
  or bounded start/stop/points equivalents. One active axis, no Cartesian scan.
  Normal scans <=161 points; DRAG <=41; spectroscopy <=241; comparison four
  fixed conditions times <=12 captures. Every generated interior point checked.
- `f12_hz`, `probe12_shape`, `probe12_length_s`, `probe12_sigma_s`,
  `probe12_amplitude`, `probe12_phase_rad`; independent prep01 fields come from
  server-frozen accepted pulse records, not mutable client copies.
- `shots` 2..2047, `acquisitions_per_point` 1..16, `capture_repeats` 1..12,
  `order_seed`; controller deadline and total-shot budget remain authoritative.
- IRB uses existing bounded `rb_lengths`, `rb_sequences`, `rb_master_seed`,
  `rb_bootstrap_replicates`, plus exact `targetPulseIdentity`. No client evidence
  acceptance fields; context resolves calibration record IDs server-side.

Reject irrelevant method fields, invalid shape/sigma units, missing provenance,
ambiguous transition identity and version mismatch before creating an attempt.
Shape Rabi does not require accepted shape Rabi (no circular prerequisite).

Plan fields: `{version:3, adapter:'science-v3', policy, operationId, requestIdentity,
resourceId, configurationBinding, profileHash, nativeSourceHashes,
extensionManifestHash, parameters, pulseIdentities, validationEvidence,
jobs, totalRows, estimated_total_shots, max_seconds, hardwareLocks, instrument,
routing}`. Each job includes native config/spec, row/variant axes, expected
logical preparations and bounded observables. Physical worker has no arbitrary
plugin/post-process path or expression evaluation from the client.

Actual result fields remain the normal controller contract:
`dataKind:'real', acquiredAt, sourceRunId, parameters, metrics,
candidateParameters, series, artifacts, analysisQuality, provenance`.
`provenance` adds policy/shape/pulse hashes, source manifests, uploaded-array
receipts, raw ADC block references, route receipt and shutdown receipt.
No `gate_fidelity` outside separately reportable IRB. No `leakage` from binary IQ.
Quality rules must be defined before method capability is available.

`build_verification_parameters` freezes the candidate and protocol; it must not
run a new optimizer in the verification attempt. Shape/f12/lambda/pi12 changes
invalidate dependent pulse identities, DRAG/IRB and SHELVE comparisons.

## IRB reuse limits

The frozen `paired_rb_support.PairedSingleQubitRB` already removes mode from seed
derivation and validates include_inverse/add_measure for a one-qubit circuit.
Its sequence_manifest and paired analysis can be reused only after confirming
new compiled gates and target insertion match the same immutable v3 pulse set.
Use a new instance compiler hook for both branches and recovery gates. Never
reuse the old policy's final fidelity as evidence for Gaussian/Cos or pi12.

The existing paired analysis returns an X/2-conditional label in current result
format; v3 must explicitly fix target X/2 for its first release, or generalize
target metadata with new tests rather than relabeling that statistic as X or
an arbitrary transition gate. PRE/POST drift and held-out calibration must still
be acquired. IRB is conditional on its noise/model assumptions and can be
nonreportable after a successful hardware acquisition.

## R13/R14: generic bias execution boundary

Implement a reusable bias-step state machine only after the RF/ADC v3 receipt
contract is stable. It can be tested with an injected transport for set →
cancellable settle → readback → RF/ADC sweep → restore/verify. This test is
software evidence and does not finish the physical requirement.

Actual LF remains disabled until a published route explicitly supplies device,
LF module/channel, unit V or A, polarity, calibrated gain, operating limits,
maximum step/slew, settle time, initial readback and restoration/hold policy.
Missing readback requires an explicit instrument capability decision and a
setpoint-only label; it cannot be silently replaced by successful set return.
Current native F8 cleanup tracks RF/ADC, so LF write support additionally needs
its own restore receipt before shared lock release. No default LF0/channel0,
no default 0 V and no inference from `fluxValue` metadata.

R14 publishes a point only after same-bias resonator and independent qubit scans
plus independent verification, with actual/readback vs setpoint distinguished.
Unnormalized ADC amplitude is readout response, not calibrated S21.

## Required review gates before enablement

1. Scalar/axis/grid/source tests, including intermediate scan points and sample
   budgets, demonstrate reject-before-attempt and reject-before-open.
2. Actual native compilation for every new circuit family, original manifest
   preservation, f12-only carrier effects, shape half rotation and RB inverses.
3. Injected hardware tests exercise the **same transport implementation** with
   fake native driver methods: upload hashes, finite trigger, fresh block data,
   sample shape validation, cancellation and cleanup failure quarantine.
4. Controller integration test proves one lease/effect owner and selection vs
   independent verification separation; direct preview cannot dispatch.
5. Root reviews the detached disabled profile, procedures/quality/source closure
   and route binding. Only then enable through existing task authorization.
6. Real accepted calibration and IRB/SHELVE outcomes are recorded separately
   from software readiness. No mock or compiled evidence closes those items.
