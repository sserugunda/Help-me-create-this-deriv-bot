export function parseSymbols(override?: string): string[] {
  if (!override) return [];
  return override
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

