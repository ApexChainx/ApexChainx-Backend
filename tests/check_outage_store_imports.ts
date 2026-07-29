// Quarantine guard for the legacy outage_store module.
// Asserts that no active source file imports from app/services/outage_store.py.
// Run with: npx ts-node tests/check_outage_store_imports.ts

import { execSync } from "child_process";
import * as path from "path";

const REPO_ROOT = path.resolve(__dirname, "..");
const LEGACY_MODULE = "outage_store";

// Directories that are allowed to reference the legacy module (e.g. this file itself).
const ALLOWED_PATHS = [
  "tests/check_outage_store_imports.ts",
  "app/services/outage_store.py", // the file itself
];

function findImporters(): string[] {
  const raw = execSync(
    `grep -r "${LEGACY_MODULE}" ${REPO_ROOT}/app --include="*.py" -l`,
    { encoding: "utf8" }
  ).trim();

  if (!raw) return [];

  return raw
    .split("\n")
    .map((p) => path.relative(REPO_ROOT, p))
    .filter((p) => !ALLOWED_PATHS.some((allowed) => p.endsWith(allowed)));
}

function main() {
  console.log(`Checking for active imports of '${LEGACY_MODULE}'...`);

  let importers: string[];
  try {
    importers = findImporters();
  } catch {
    // grep exits non-zero when no matches found
    importers = [];
  }

  if (importers.length > 0) {
    console.error("Legacy outage_store is still imported by:");
    importers.forEach((f) => console.error(`  ${f}`));
    console.error("Remove these imports before merging.");
    process.exit(1);
  }

  console.log("No active imports of legacy outage_store found. Safe to quarantine.");
}

main();
