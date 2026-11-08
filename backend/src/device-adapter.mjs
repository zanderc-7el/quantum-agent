/** In-process stand-in for a connected measurement controller. No hardware I/O. */
export function createSimulatedDevice({ readoutHz, fault = null } = {}) {
  let valueHz = readoutHz;
  let stopFailurePending = fault === "stop_unconfirmed";
  return {
    descriptor: () => ({ adapterId: "simulated-controller-v1", connected: true,
      hardwareConnected: false, dataSource: "simulator", fault: fault ?? "none",
      capabilities: ["acquire_chunk", "stop_ack", "write_readout_frequency", "readback_readout_frequency"] }),
    acquireChunk({ full, offset, limit, commandId }) {
      if (fault === "acquisition_interrupt" && offset >= 10) throw new Error("SIMULATED_ACQUISITION_INTERRUPT");
      return { points: full.slice(offset, offset + limit), receipt: { deviceId: "simulated-controller-v1",
        command: "acquire_chunk", commandId, offset, accepted: true } };
    },
    stop({ commandId } = {}) {
      if (stopFailurePending) { stopFailurePending = false; return { commandId, confirmed: false, reason: "SIMULATED_STOP_TIMEOUT" }; }
      return { commandId, confirmed: true, deviceId: "simulated-controller-v1" };
    },
    writeReadoutFrequency(nextHz) { valueHz = nextHz; return { accepted: true, deviceId: "simulated-controller-v1" }; },
    readbackReadoutFrequency() { return fault === "readback_mismatch" ? valueHz + 1_000_000 : valueHz; },
  };
}
