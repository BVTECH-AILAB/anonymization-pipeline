import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { ProviderSessione } from "./stato/sessione";
import "./stile.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <ProviderSessione>
        <App />
      </ProviderSessione>
    </BrowserRouter>
  </StrictMode>,
);
