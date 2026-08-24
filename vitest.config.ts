import path from "node:path";
import { defaultExclude, defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    // `*.spec.ts` files under tests/ are Playwright tests and must only be
    // run by `npm run test:e2e`, never by vitest.
    exclude: [...defaultExclude, "**/*.spec.ts"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
