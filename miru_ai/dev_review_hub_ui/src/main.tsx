import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./vaul-drawer.css";
import "./index.css";
import App from "./App";
import { MiruNavBar } from "@/components/hub/MiruNavBar";

/** Same nav as Hub / Operator when miru_ai Jinja nav is not used (e.g. /dev). */
const unifiedNav = document.getElementById("miru-unified-nav-root");
if (unifiedNav) {
  createRoot(unifiedNav).render(
    <StrictMode>
      <MiruNavBar />
    </StrictMode>,
  );
}

const el =
  document.getElementById("shadow-review-root") ??
  document.getElementById("operator-root") ??
  document.getElementById("hub-root") ??
  document.getElementById("dev-training-root") ??
  document.getElementById("root");
if (el) {
  createRoot(el).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
