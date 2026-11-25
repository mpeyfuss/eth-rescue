import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  base: "./",
  // The plan schema and examples live at the repo root.
  server: { fs: { allow: [".."] } },
});
