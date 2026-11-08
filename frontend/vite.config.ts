import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";

// One source of truth for the product shell, available in dev and build.
const productFile = new URL("../prototype/quantamind-product.html", import.meta.url);
export default defineConfig({
  plugins: [react(), {
    name: "quantamind-product-shell",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.url?.split("?")[0] !== "/product.html") return next();
        res.setHeader("Content-Type", "text/html; charset=utf-8");
        res.end(readFileSync(productFile, "utf8"));
      });
    },
    generateBundle(_options, bundle) {
      const entry = Object.values(bundle).find(item => item.type === "chunk" && item.isEntry && item.name === "product");
      if (!entry) throw new Error("Missing product module entry");
      this.emitFile({ type: "asset", fileName: "product.html", source: readFileSync(productFile, "utf8").replace("/src/product.tsx", "./" + entry.fileName) });
    },
  }],
  server: { port: 5286, strictPort: true, proxy: { "/api/": "http://127.0.0.1:5290", "/health": "http://127.0.0.1:5290" } },
  preview: { port: 5286, strictPort: true },
  build: { sourcemap: true, rollupOptions: { input: { main: new URL("./index.html", import.meta.url).pathname, product: new URL("./src/product.tsx", import.meta.url).pathname } } },
});
