"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.config = void 0;
require("dotenv/config");
const zod_1 = require("zod");
const EnvSchema = zod_1.z.object({
    DERIV_APP_ID: zod_1.z.string().min(1, 'DERIV_APP_ID is required'),
    DERIV_API_TOKEN: zod_1.z.string().min(1, 'DERIV_API_TOKEN is required'),
    TELEGRAM_BOT_TOKEN: zod_1.z.string().min(1, 'TELEGRAM_BOT_TOKEN is required'),
    TELEGRAM_CHAT_ID: zod_1.z.string().optional(),
    SYMBOLS: zod_1.z.string().optional(), // comma separated override
    STAKE_PCT: zod_1.z
        .string()
        .optional()
        .transform((v) => (v ? Number(v) : 1))
        .pipe(zod_1.z.number().min(0.1).max(100)),
    GRANULARITY: zod_1.z
        .string()
        .optional()
        .transform((v) => (v ? Number(v) : 60))
        .pipe(zod_1.z.number().refine((n) => [60].includes(n), 'Only 60s is supported')),
    PERC_STEP: zod_1.z
        .string()
        .optional()
        .transform((v) => (v ? Number(v) : 1))
        .pipe(zod_1.z.number().min(0).max(5)),
    MIN_SAMPLES: zod_1.z
        .string()
        .optional()
        .transform((v) => (v ? Number(v) : 1))
        .pipe(zod_1.z.number().min(1)),
});
const parsed = EnvSchema.safeParse(process.env);
if (!parsed.success) {
    const issues = parsed.error.issues
        .map((i) => `${i.path.join('.')}: ${i.message}`)
        .join(', ');
    throw new Error(`Invalid environment variables: ${issues}`);
}
exports.config = {
    derivAppId: parsed.data.DERIV_APP_ID,
    derivApiToken: parsed.data.DERIV_API_TOKEN,
    telegramBotToken: parsed.data.TELEGRAM_BOT_TOKEN,
    telegramChatId: parsed.data.TELEGRAM_CHAT_ID,
    symbolsOverride: parsed.data.SYMBOLS,
    stakePct: parsed.data.STAKE_PCT / 100, // convert to 0..1
    granularity: parsed.data.GRANULARITY,
    percStep: parsed.data.PERC_STEP,
    minSamples: parsed.data.MIN_SAMPLES,
};
//# sourceMappingURL=config.js.map