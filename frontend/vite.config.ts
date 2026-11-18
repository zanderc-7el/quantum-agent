import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  server: { port: 5286, strictPort: true, proxy: { "/api": { target: "http://127.0.0.1:8765", changeOrigin: false } } },
  preview: { port: 5286, strictPort: true },
  build: { sourcemap: true },
});
