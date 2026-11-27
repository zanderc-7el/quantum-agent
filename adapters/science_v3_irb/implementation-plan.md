# Isolated third batch: independent DRAG X/2 IRB

Authorized by root on 2026-11-26. The six-method V3 manifest is frozen at
16e986e45cb0a227ae2314d474767a9f55a81838cb78544f982fb65ef30af62c.
No six-method production Python file, old runtime, driver or manifest is edited.

1. Test and implement native paired sequence manifests, actual primitive-to-v3
   pulse mapping, deterministic row order and full timing/sample/shot budgets.
2. Test PRE-only LDA training with separate held-out captures; freeze before
   branch acquisition. Analyze the native paired RB fits with POST drift gates.
3. Implement an independent adapter/profile and fixed worker using the existing
   controller lease and native V3 transport. One operation covers all stages;
   all precompiled arrays, upload receipts and fresh blocks retain row identity.
4. Test actual native compilation, independent verification seeds, reject-before-
   open, stopping and cleanup failure with injected hardware only. No real device.
5. Write an integration contract/patch separately. Default disabled and
   unregistered until root and scientific reviewer accept this batch.

Pulse policy stays V3 fixed-duration, amplitude-scaled half rotations. Every
native reference Clifford, interleaved X/2 and inverse uses the same frozen
shape/sigma/phase/lambda/Delta and amplitude identity. Native identity Clifford
has no emitted primitive (zero physical duration), explicitly recorded in the
manifest; no hidden DC/half-duration compatibility hook is used.

Default campaign is 20 sequences x 5 lengths x 2 modes x 2 captures, plus four
PRE captures and four POST captures of both states: 416 rows. This remains under
the existing 400-million sample cap at 83 us windows. PRE first two captures train;
last two provide held-out capture-level checks. Exponential/model uncertainty and
capture-level reference uncertainty remain separate; no gate fidelity if either
reference, drift, sequence identity, fit or ratio-interval checks fail.

The first release target is exactly X/2. The gate fidelity is conditional on the
native RB assumptions and is not three-state leakage or process tomography.
Independent verification repeats the complete operation with a fresh master seed
and never uses the selection operation's PRE, raw data or branch circuits.

Final admission extension: root authorized narrow legacy DC time-Rabi working-point
and time-window lineage. It retains the authenticated continuous fit and model
uncertainty, verifies the old 0.5 ns committed duration, and explicitly rounds the
shaped window to 0.25 ns with at most 0.125 ns error. Shape-pi and half-pi records
still calibrate the actual rotation. Both half-DRAG operations freeze the same
lineage; IRB reauthenticates its complete source closure. The scientific reviewer
and independent controller/quality contract tests accepted this extension before
the two methods became conditionally available. New profiles remain disabled.
