import test from "node:test";
import assert from "node:assert/strict";
import {
  ApiContractError,
  Documented32ApiClient,
  documentedCapabilityAssessment,
} from "../src/lib/apiOnly/contract.mjs";
import { createMock32Transport } from "../src/lib/apiOnly/mock32.mjs";
import { proposeFollowup, runValidation } from "../src/lib/apiOnly/validation-core.mjs";

test("documented API-only mock exercises create, query, result and cancel without SDK", async () => {
  const report = await runValidation();
  assert.equal(report.mode, "offline-in-process-api-mock-no-hardware-no-sdk");
  assert.equal(report.generatedProposal.proposedCenterHz, 6.10e9);
  assert.equal(report.fullAutoCalibrationReady, false);
  assert.equal(report.mockCallsPassed.length, 6);
});

test("business-level failure is not mistaken for a successful HTTP response", async () => {
  const mock = createMock32Transport({ failPath: "/task/insert" });
  const client = new Documented32ApiClient(mock.baseUrl, mock.fetchImpl);
  await assert.rejects(
    client.submitTask({ resourceId: 1, taskDetailDTO: { quantumCode: "mock-only" } }),
    (error: unknown) => error instanceof ApiContractError && error.details.code === 202,
  );
});

test("the mock cannot silently validate undocumented parameter publishing", async () => {
  const mock = createMock32Transport();
  const client = new Documented32ApiClient(mock.baseUrl, mock.fetchImpl);
  await assert.rejects(client.request("POST", "/calibration/publishParameters", { body: {} }), ApiContractError);
  assert.equal(documentedCapabilityAssessment.fullAutoCalibrationReady, false);
});

test("malformed result data cannot produce a follow-up proposal", () => {
  assert.throws(() => proposeFollowup([{ id: 1, xAxisData: [1, 2], list: [{ values: [1] }] }]));
});
