# Third batch: exact half-gate DRAG and independent IRB

This package adds `ScienceIRBAdapter(root, profile)` and
`build_irb_profile(root, base_profile)`. The reviewed methods are conditionally
available; a new profile still starts with `enabled=false`.
The earlier native runtime and six-method science policy remain frozen. No
hardware was connected by the implementation or its test fixtures.

## Methods and prerequisites

| Method | Prerequisite roles | Result |
| --- | --- | --- |
| `sq_drag_half_scan` | pi01, half01, anharmonicity, readout, rabi_amplitude, rabi_time | Select `drag_lambda1` for the exact X/2 pulse, then independently reacquire the frozen candidate |
| `sq_drag_irb` | pi01, half01, drag, anharmonicity, readout | Conditional X/2 interleaved RB estimate; produces no new calibration parameter |

The IRB `drag` source must be an accepted `sq_drag_half_scan` with the exact
target pulse identity, including half amplitude, rotation, lambda, shape, sigma,
duration, signed anharmonicity, carrier, buffer and readout context. A full-pi
DRAG diagnostic does not meet this prerequisite. Source receipts, raw hashes,
parameter values, configuration or observed-resource lineage and upload receipts
remain subject to the existing native authenticator and server dependency resolver.

The amplitude source must be the same exact shaped-pi record used by pi01. The
time source is a separately authenticated legacy DC time-Rabi record, used only
as working-point and time-window support. Its native double-operation receipts,
DC shape, two-nanosecond buffer, disabled DRAG, sample grid, frequency and readout
conditions are checked. The source's continuous fitted duration and per-repeat
model uncertainty are preserved separately from its old 0.5 ns committed duration.
The shaped window is the nearest 0.25 ns sample, with at most 0.125 ns error and
ties rounded to even. Both quantization policies are explicit; DC time-Rabi does
not calibrate the shaped rotation. The exact shape-pi and half-pi sources provide
that evidence. IRB requires the same lineage in both half-DRAG native receipts
and reauthenticates every required ancestor before executing.

Half-DRAG uses `(X/2 Y/2)^3` identity blocks. The scan must contain an acquired
lambda=0 half-gate baseline. Its default selection has 92 rows; verification has
96, including one fixed-candidate diagnostic per capture. Verification records
the actual zero-baseline-minus-candidate comparison in
`provenance.scienceHalfDrag.candidateComparison`. The sign convention in this
object is zero-baseline response minus candidate response, so positive values
indicate diagnostic improvement. `diagnostic_improvement` separately means the
zero-axis response minus the minimum observed scan-axis response.

IRB defaults are lengths `[1,4,16,64,128]`, 20 independent sequences per length,
two captures per branch, and four two-state captures in each reference stage:
416 total rows. All requested rows, shots and samples count toward the operation
budget; no parameter is silently reduced. Selection and verification use
different master seeds, with the same pulse identity and independent PRE/raw data.

## Native pipeline and limits

One controller operation and one shared hardware lease cover PRE, paired RB/IRB
and POST. The original native Clifford generator produces seeds, inverse gates
and closure manifests. The third compiler uses actual native DrivePulse,
Sequence and Wave mixing, combines only nonoverlapping envelopes on the same
carrier, and checks compiled sample hashes again immediately before each upload.
Tests compare its wave bytes with the frozen segmented V3 compiler.

The worker archives all preflight arrays, sequence manifests, every fresh ADC
block, upload receipts and stage raw arrays. A stop or deadline is honored at
finite block boundaries. Captured data is retained; cleanup failure retains the
operation's locks for reconciliation. The limits are 3,000 rows, 400 million
compiled channel samples, 10 million shots, 100,000 primitive tokens, a 79.7 us
control window per circuit, and at most 3,600 seconds per operation. The isolated
planning subprocess has a 45-second limit and bounded JSON input/output.

The existing worker environment is pinned to Python 3.12.10, NumPy 2.5.2 and
SciPy 1.18.1. `runtime_contract.py` checks it before execution locks are acquired
and writes `native_runtime.json`. API-side parameter and quality checks use the
standard library; installing NumPy or SciPy into API Python313 is unnecessary.

## Scientific acceptance scope

The first two PRE captures train a projection. The other two are held out;
individual capture contrast and offset checks must pass before any RB branch is
uploaded. POST uses the frozen PRE scale, with no refit or clipping. Native paired
seed bootstrap intervals, identifiable decay fits, both R-squared values and
reference drift gates all contribute to acceptance. Rejected results do not
publish a final gate-fidelity metric.

This is a conditional interleaved RB estimate under the recorded RB model and
prepared-state reference assumptions. It does not measure leakage, three-state
population, process tomography or an unconditional gate fidelity. The numeric
fixtures exercise these contracts and are explicitly ineligible as calibration
or measured evidence.

## Verification entry points

From the candidate root:

```powershell
& D:/Python313/python.exe -B -m unittest tests.test_science_v3_irb tests.test_science_v3_irb_analysis tests.test_science_v3_irb_native -v
```

The analysis and native tests delegate to the configured existing Python312,
with networking, child-process creation and instrument imports blocked in the
test environment. The native suite uses an explicitly injected hardware fixture
while executing the actual native compiler, SequenceRunner, repeated acquisition
handling and cleanup. It includes all 416 IRB rows, 832 fresh ADC blocks, half-gate
selection/verification, cancellation, cleanup failure, failed PRE admission,
independent seeds and compiled-byte identity. The synthetic quality fixture helper
is `tests/test_science_v3_irb_fixture.py`; it does not generate measurement records.
