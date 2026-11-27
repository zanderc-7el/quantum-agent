# Bridge-only manifest migration, 2026-11-26

The root agent explicitly authorized this dependency pin update after deploying
the bounded progress-event delivery fix. No native compiler, pulse, sequence,
instrument driver or numerical policy is changed by this migration.

- Former configurable-runtime manifest: 107 entries,
  `cd7bbd9192aca7f578c6e05afd90b8f8aa7734e1b0ed4bf6306488ce68ceb06e`.
- New configurable-runtime manifest: 108 entries,
  `45278b474941a05f0db0585efaeeb538b7bbe46f8125e7b8191c50e296f4811b`.
- Changed dependency: platform bridge `adapters/arclight.py` progress delivery;
  new dependency: `adapters/progress_delivery.py`.
- Original 99-entry runtime manifest remains
  `1531ee2cd78774e6c6a5e089be54c3f35a98d8c0fdf03aa0547169106527a322`.

All 108 referenced files were hash-checked before changing the offline pin.
The offline extension manifest changes only the policy.py hash for this migration.
Existing acceptance.json and compiled-array records retain their original source
hashes and remain historical evidence of that source closure. New compilation
receipts bind the new closure. V3 development sources are not added to the offline
extension manifest. This is an explicit source version migration, not disabled
integrity verification or a fallback to an unverified manifest.

## Second authorized bridge-only migration

The subsequent candidate bridge repair preserves process supervision and stop
handling when events.jsonl cannot be read or parsed. Only adapters/arclight.py
changed; progress_delivery.py, the native compiler and all drivers did not.
All 108 manifest files were checked again before updating the pin.

- Previous manifest: 45278b474941a05f0db0585efaeeb538b7bbe46f8125e7b8191c50e296f4811b
- New manifest: 62a0870c30edec3432613f645c4de7e063e1132ba510b71f7a71fd431ed83f2a
- New arclight.py: 653e39e873b759dd0919c40600ab2e665364d2dfc9b578ba46f118a9f2f405b7

This update is candidate-only; deployment remains with the root agent. Existing
compile and V3 acceptance records retain the hashes used at their execution.
