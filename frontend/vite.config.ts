import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// Backend location for the dev proxy; override with VITE_API_TARGET (e.g. in frontend/.env.local)
// when port 8000 is taken by something else.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.VITE_API_TARGET || "http://localhost:8000";
  return {
    plugins: [react()],
    server: {
      proxy: {
        "/api": target,
        "/ws": { target: target.replace(/^http/, "ws"), ws: true },
      },
    },
  };
});
