// Isolated component rendering: no application server, API, or instrument requests.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const { buildSync } = require("esbuild");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const { chromium } = require(process.env.QCAL_PLAYWRIGHT_ROOT || "C:/Program Files/WindowsApps/OpenAI.CodexPrimaryRuntime.v26-1112-611-0_26.1112.611.0_x64__3k8sg7r9htsxt/dependencies/node/node_modules/playwright");

const frontend = path.resolve(__dirname, "..");
const compiled = buildSync({
  absWorkingDir: frontend, entryPoints: ["src/components/RealFlowCanvas.tsx"],
  bundle: true, write: false, platform: "node", format: "cjs", packages: "external",
  jsx: "automatic", loader: { ".css": "empty" },
}).outputFiles[0].text;
const component = new Module(path.join(__dirname, "flow-edge-color-component.cjs"), module);
component.filename = component.id;
component.paths = module.paths;
component._compile(compiled, component.filename);
const RealFlowCanvas = component.exports.default;
// Put global theme styles last to verify specificity against their older status colors.
const css = ["components/flow-canvas.css", "components/remote.css", "theme.css"]
  .map(file => fs.readFileSync(path.join(frontend, "src", file), "utf8")).join("\n");
const nodes = [
  { id: "rabi", label: "Rabi 幅度", dependsOn: [] },
  { id: "time", label: "时间 Rabi", dependsOn: ["rabi"] },
  { id: "iq", label: "IQ 判别", dependsOn: ["rabi", "time"] },
  { id: "readout", label: "读出优化", dependsOn: ["iq"] },
].map(node => ({ ...node, name: node.id, status: "pending", progress: 0, attempt: 0, qubits: "Q11", detail: "", ownerId: "Q11" }));
const workflow = { id: "isolated-flow-colors", target: "Q11", mode: "real", nodes,
  currentNodeId: "iq", edges: [{ from: "readout", to: "iq", kind: "backtrack" }] };

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.QCAL_BROWSER_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true, args: ["--disable-background-networking"] });
  const results = [];
  try {
    const page = await browser.newPage();
    await page.route("**/*", route => route.abort());
    for (const theme of ["dark", "light"]) {
      const expected = theme === "dark" ? ["rgb(107, 157, 255)", "rgb(178, 154, 231)", "rgb(239, 166, 93)"]
        : ["rgb(53, 109, 197)", "rgb(132, 85, 181)", "rgb(185, 101, 35)"];
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 900 });
        for (const status of ["running", "attention", "paused", "completed", "aborted"]) {
          const html = renderToStaticMarkup(React.createElement(RealFlowCanvas, { workflow: { ...workflow, status }, selectedNodeId: "iq", onSelectNode() {} }));
          await page.setContent(`<html data-theme="${theme}"><head><style>${css}</style></head><body style="margin:0">${html}</body></html>`);
          const observations = await page.evaluate(() => {
            const inspect = element => { const style = getComputedStyle(element); return { color: style.stroke, dash: style.strokeDasharray, width: style.strokeWidth, filter: style.filter }; };
            return {
              kinds: ["main", "dependency", "rollback"].map(kind => {
                const links = [...document.querySelectorAll(`.fc-connections .fc-edge--${kind}`)];
                return { kind, links: links.map(inspect), active: links.filter(link => link.classList.contains("fc-edge--active")).length,
                  swatch: inspect(document.querySelector(`.fc-edge-swatch .fc-edge--${kind}`)) };
              }),
              edgeCount: document.querySelectorAll(".fc-connections .fc-edge").length,
              legendFits: [...document.querySelectorAll(".rp-flow-legend > span")].every(span => span.getBoundingClientRect().right <= innerWidth),
              pageFits: document.documentElement.scrollWidth <= innerWidth,
            };
          });
          assert.equal(observations.edgeCount, 5);
          assert.equal(observations.legendFits, true);
          assert.equal(observations.pageFits, true);
          observations.kinds.forEach((kind, index) => {
            assert.ok(kind.links.length > 0);
            assert.ok(kind.links.every(link => link.color === expected[index]), `${theme}/${status}/${kind.kind} retains relation color`);
            assert.equal(kind.active, ["running", "attention", "paused"].includes(status) ? 1 : 0);
            assert.equal(kind.swatch.color, expected[index]);
            assert.ok(kind.links.every(link => link.dash === kind.swatch.dash));
          });
          assert.equal(observations.kinds[0].links[0].dash, "none");
          assert.notEqual(observations.kinds[1].links[0].dash, observations.kinds[2].links[0].dash);
          assert.ok(Number.parseFloat(observations.kinds[0].links[0].width) > Number.parseFloat(observations.kinds[1].links[0].width));
          results.push({ theme, width, status, colors: expected, edgeCount: observations.edgeCount });
        }
      }
    }
    console.log(JSON.stringify({ passed: results.length, themes: ["dark", "light"], widths: [1440, 390],
      statuses: ["running", "attention", "paused", "completed", "aborted"],
      checks: "edge and legend colors/dashes, active status, route preservation, desktop/mobile overflow" }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
