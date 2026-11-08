import { runValidation } from "../../src/lib/apiOnly/validation-core.mjs";

runValidation().then(
  (report) => process.stdout.write(`${JSON.stringify(report, null, 2)}\n`),
  (error) => { process.stderr.write(`${error.stack || error}\n`); process.exitCode = 1; },
);
