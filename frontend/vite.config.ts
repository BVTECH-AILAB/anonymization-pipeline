/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development /api goes to the local API (uvicorn on :8000, or ANONPIP_API_URL); in production nginx
// does it (nginx.conf). Both strip the /api prefix.
const API = process.env.ANONPIP_API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": { target: API, rewrite: (p) => p.replace(/^\/api/, "") } },
  },
  test: { environment: "jsdom" },
});
