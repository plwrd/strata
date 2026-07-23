import react from "@vitejs/plugin-react";
import { resolve, dirname } from "node:path";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";

const rootDir = dirname(fileURLToPath(import.meta.url));

// The bundle is loaded from `strata://app/index.html` inside Qt WebEngine, never
// from a web server. Relative asset paths are therefore mandatory: an absolute
// `/assets/...` would resolve against the scheme root and (worse) tempt someone
// into serving the app over HTTP later.
export default defineConfig({
  base: "./",
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    target: "chrome120", // Qt 6.8 ships Chromium 122
    sourcemap: true,
    rollupOptions: {
      input: {
        main: resolve(rootDir, "index.html"),
        humazie: resolve(rootDir, "humazie.html"),
      },
      output: {
        manualChunks: {
          three: ["three", "@react-three/fiber", "@react-three/drei"],
        },
      },
    },
  },
  worker: {
    format: "es",
  },
  server: {
    port: 5173,
    strictPort: true,
    host: "127.0.0.1", // dev server never binds to a public interface
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/tests/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // Auto-restore spies between tests so a `vi.spyOn` in one file can never
    // leak into another and cause order-dependent flakes.
    restoreMocks: true,
    coverage: {
      reporter: ["text", "lcov"],
    },
  },
});
