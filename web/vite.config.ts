import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// ponytail: dev proxy instead of asking the backend for CORS. The arena server is
// loopback-only and unauthenticated, so same-origin through Vite is the smaller change.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { "/v1": { target: process.env.ARENA_API ?? "http://127.0.0.1:8765" } },
  },
});
