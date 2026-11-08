import React from "react";
import { createRoot } from "react-dom/client";
import { WorkspaceApp } from "./App";
import { createServerDemoGateway } from "./lib/serverDemoGateway";
import { getUserById } from "./lib/auth";

// Native page modules. One React instance retains shared workflow state.
const host = document.getElementById("product-module")!;
host.replaceChildren();
const shadow = host.attachShadow({ mode: "open" });
const css = import.meta.glob("./**/*.css", { eager: true, query: "?inline", import: "default" });
const ordered = ["./styles.css", "./theme.css", "./quantamind-theme.css"];
const paths = [...Object.keys(css).filter(path => !ordered.includes(path)), ...ordered];
const style = document.createElement("style");
style.textContent = paths.map(path => String(css[path] || "").replaceAll(":root", ":host").replace(/\bbody\b/g, ":host")).join("\n") + `
:host { display:block; color-scheme:dark; font-family:Inter,"Segoe UI","Microsoft YaHei",sans-serif; }
:host([hidden]) { display:none; }
.product-integrated > .sidebar, .product-integrated .global-bar,
.product-integrated .app-footer, .product-integrated .role-workspace-banner,
.product-integrated .blueprint-library, .product-integrated .blueprint-stages,
.product-integrated .blueprint-timeline { display:none; }
.product-integrated { min-height:calc(100vh - 70px); }
.product-integrated > .main-shell { margin-left:0; width:100%; min-width:0; }
.product-engine-status { padding:8px 25px; color:#91a4ba; background:#101820; border-bottom:1px solid #293642; font-size:12px; }
.product-engine-status[data-connected="false"] { color:#efbd75; }
.product-taskbar { display:flex; align-items:center; gap:12px; padding:12px 25px; border-bottom:1px solid var(--line); }
.product-taskbar select { min-width:160px; max-width:440px; flex:1; background:var(--panel); color:var(--text); padding:8px; border:1px solid var(--line); border-radius:6px; }
.product-taskbar label { font-size:12px; color:var(--muted); }
.product-integrated .cr-host { min-height:calc(100vh - 150px); }
.product-integrated .heading-actions { flex-wrap:wrap; }
@media(max-width:900px) { .product-taskbar { flex-wrap:wrap; } }
`;
shadow.append(style);
const status = document.createElement("div");
status.className = "product-engine-status";
status.setAttribute("role", "status");
status.textContent = "正在连接服务端模拟引擎…";
shadow.append(status);
const root = document.createElement("div");
shadow.append(root);
// Only the local Docker simulation API; never targets a real backend.
const gateway = createServerDemoGateway({ onStatus: (message, connected) => { status.textContent = message; status.dataset.connected = String(connected); } });
createRoot(root).render(<WorkspaceApp productMode gateway={gateway} user={getUserById("leader")!} onLogout={() => {}} />);
