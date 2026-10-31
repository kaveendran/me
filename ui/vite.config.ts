import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `me serve` serves dist/ from an arbitrary path and injects the token into
// index.html, so every asset URL must be relative.
export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    proxy: {
      // changeOrigin rewrites Host to 127.0.0.1:4777, which the server allows.
      "/api": { target: "http://127.0.0.1:4777", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
