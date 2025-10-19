"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.parseSymbols = parseSymbols;
function parseSymbols(override) {
    if (!override)
        return [];
    return override
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
}
//# sourceMappingURL=symbols.js.map