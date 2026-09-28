import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // Recharts drags in a dozen d3 packages and roughly trebled the single
        // bundle. Splitting it out means the login screen and the tables no
        // longer pay for charting code they never touch, and the vendor chunks
        // stay cached across deploys of app code.
        manualChunks: {
          charts: ["recharts"],
          motion: ["motion"],
          vendor: ["react", "react-dom", "react-router-dom"],
        },
      },
    },
  },
  server: {
    port: 5173,
    // The session cookie is HttpOnly and same-site; proxying /api through the
    // dev server keeps the browser on one origin so the cookie is sent.
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
