import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import RemoteApp from "./RemoteApp";
import "./styles.css";
import "./theme.css";
import "./components/remote.css";
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {new URLSearchParams(location.search).get("demo") === "1" ? <App /> : <RemoteApp />}
  </React.StrictMode>,
);
