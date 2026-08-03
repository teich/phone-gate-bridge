import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import "./dashboard.css";

const root = document.getElementById("root");
if (root === null) throw new Error("Dashboard root element is missing");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
