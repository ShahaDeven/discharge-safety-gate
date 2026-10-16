import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: `npm run dev` on :5175, API calls proxied to the FastAPI server on :8001.
export default defineConfig({
  plugins: [react()],
  server: { port: 5175, proxy: { "/api": "http://localhost:8001" } },
});
