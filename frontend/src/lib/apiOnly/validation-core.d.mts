export interface ApiOnlyReport {
  mode: string;
  httpTransportVerified: boolean;
  vendorContractVerified: boolean;
  mockCallsPassed: string[];
  cancelResponseData: boolean;
  sampleCurve: { frequenciesHz: number[]; amplitudes: number[] };
  generatedProposal: {
    sourceDataId: number;
    proposedCenterHz: number;
    rationale: string;
    executableWithDocumentedContract: boolean;
  };
  demonstratedRoutes: string[];
  unverifiedForFullAutomation: string[];
  fullAutoCalibrationReady: boolean;
  conclusion: string;
}

export function runValidation(): Promise<ApiOnlyReport>;
