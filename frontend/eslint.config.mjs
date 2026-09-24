import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";

export default defineConfig([
  ...nextVitals,
  {
    rules: {
      // The flagged effects read localStorage after hydration (the page is
      // prerendered, so useState initialisers would mismatch), start the
      // post-login fetch, and reset the always-mounted transaction modal.
      // Each is deliberate; keep them visible without failing the lint.
      "react-hooks/set-state-in-effect": "warn"
    }
  },
  globalIgnores([".next/**", "out/**", "build/**"])
]);
