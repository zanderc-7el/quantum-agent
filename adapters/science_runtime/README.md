# Native offline compiler v1

Frozen first increment, 2026-11-26. This is a compile service, **not** a hardware
execution adapter, a calibration, a measured DRAG optimum or a SHELVE readout
improvement. The physical execution increment belongs in a separate v3 package.

## Actual implementation

`backend.native_preview.NativePreviewService(controller).preview(body)` accepts
`kind: pulse|drag|shelve`, `scope: {workflowId,resourceId,chipVersionId?,deploymentVersionId?}`,
optional `nodeId`, and scalar-only `input`. Multi-resource workflows require a
node. Server-held parameter revisions, resource, profile, route and configuration
hashes are frozen; changes during compilation reject the result.

Input names: shape (`DC`, `Gaussian`, `CosPulse`), durationNs, amplitude,
phaseRad, sigmaNs (mandatory for Gaussian), lambda1, anharmonicityHz,
anharmonicitySource (mandatory for nonzero lambda1), frequencyHz, gapNs,
readoutDelayNs, readoutWidthNs, readoutAmplitude, readoutFrequencyHz,
adcDelayNs, rotationRad, virtualZRad. SHELVE additionally requires f12Hz and
accepts probeDurationNs/probeAmplitude. Inputs are explicitly unvalidated
compile parameters; a text source label is never accepted calibration evidence.

The configured existing Python 3.12 interpreter runs `compile_only_worker.py`
with `-I -B` in a private artifact directory. Both frozen runtime manifests
(99 and 107 files) and this extension manifest are checked before native import
and after compilation. No broad native package initialization or instrument
driver import occurs. Numeric source modules are loaded from verified native
files; the runner dependency is a fail-closed device-resolution stub.

Actual native Compiler decomposition and gate phase conventions, DrivePulse
envelope/gradient, Sequence ASAP placement and readout construction, and Wave
addition/mixing are used. The extension forwards sigma in seconds and calls
native Wave.mix for each segment at its own frequency on absolute sequence time.
It does not replace native shapes with independent analytical preview formulas.

The policy uses a fixed 2 ns total buffer and fixed envelope duration for half
rotations, with amplitude scaled by theta/pi. This differs from the old short-half
DC policy and does not modify it. Native modulo-2pi behavior makes -pi and +pi
equivalent; negative half rotations carry the native pi phase reversal.

Readout starts at native probT0=80 us plus readoutDelayNs; simultaneously adjusting
gapTime keeps XY fixed. ADC starts at readout start plus adcDelayNs. These are
compiler/configuration timing values, not measured cable/instrument delays.
Readout retains the native 200 ns pre-pulse and half-amplitude main portion.
Gaussian endpoints are the native non-subtracted Gaussian endpoints.

Each write-once output folder contains request.json, native_config.json,
waveforms.npz (full-resolution XY RF, readout I/Q, and local I/Q/RF per pulse),
result.json, compile_only.log and, through the service, record.json. Artifact
downloads verify SHA256. XY display samples are capped at 2048 with explicit
stride; full arrays remain in NPZ. Digital alias frequency and physical carrier
are distinguished; RF passband is not inferred from the discrete spectrum.

Bounds: 2 concurrent service workers, 25-second timeout, at most 650,000 aggregate
XY/readout samples, duration <=2 us per XY envelope, <=10 us readout. Grid,
peak and clipping checks reject instead of normalizing or truncating. Cancellation
or timeout kills and waits for the compile child and persists a failure receipt.
No station lock, preflight, upload, execution or model API is invoked.

## Verified

`python -B -m unittest tests.test_native_preview tests.test_native_preview_service tests.test_arclight_profile_v2 tests.test_arclight_adapter -v`

42 tests passed (16 new native compile/service contracts and 26 legacy adapter
contracts). New tests execute the actual existing Python312/native compiler:
20 ns envelope + 2 ns buffer = 88 samples at 4 GHz; signed DRAG derivative;
explicit Gaussian sigma; native CosPulse samples; peak rejection; fixed-duration
half rotations/virtual-Z; independent ADC/RO delay; per-segment f01/f12 mixing;
prep/readout invariance under f12 changes; scalar/grid limits; source tamper;
immutable outputs/hash download; pre/mid cancellation and actual process timeout;
network/subprocess/VISA blocking; scope and changed-parameter rejection.

Independent read-only review by science_review_1125 found no blocking issue.
This test coverage is software/native numeric evidence, not live hardware evidence.
