import 'dotenv/config';
import { z } from 'zod';

const EnvSchema = z.object({
  DERIV_APP_ID: z.string().min(1, 'DERIV_APP_ID is required'),
  DERIV_API_TOKEN: z.string().min(1, 'DERIV_API_TOKEN is required'),
  TELEGRAM_BOT_TOKEN: z.string().min(1, 'TELEGRAM_BOT_TOKEN is required'),
  TELEGRAM_CHAT_ID: z.string().optional(),
  SYMBOLS: z.string().optional(), // comma separated override
  STAKE_PCT: z
    .string()
    .optional()
    .transform((v) => (v ? Number(v) : 1))
    .pipe(z.number().min(0.1).max(100)),
  GRANULARITY: z
    .string()
    .optional()
    .transform((v) => (v ? Number(v) : 60))
    .pipe(z.number().refine((n) => [60].includes(n), 'Only 60s is supported')),
  PERC_STEP: z
    .string()
    .optional()
    .transform((v) => (v ? Number(v) : 1))
    .pipe(z.number().min(0).max(5)),
  MIN_SAMPLES: z
    .string()
    .optional()
    .transform((v) => (v ? Number(v) : 1))
    .pipe(z.number().min(1)),
});

const parsed = EnvSchema.safeParse(process.env);
if (!parsed.success) {
  const issues = parsed.error.issues
    .map((i) => `${i.path.join('.')}: ${i.message}`)
    .join(', ');
  throw new Error(`Invalid environment variables: ${issues}`);
}

export const config = {
  derivAppId: parsed.data.DERIV_APP_ID,
  derivApiToken: parsed.data.DERIV_API_TOKEN,
  telegramBotToken: parsed.data.TELEGRAM_BOT_TOKEN,
  telegramChatId: parsed.data.TELEGRAM_CHAT_ID,
  symbolsOverride: parsed.data.SYMBOLS,
  stakePct: parsed.data.STAKE_PCT / 100, // convert to 0..1
  granularity: parsed.data.GRANULARITY,
  percStep: parsed.data.PERC_STEP,
  minSamples: parsed.data.MIN_SAMPLES,
} as const;

export type AppConfig = typeof config;

