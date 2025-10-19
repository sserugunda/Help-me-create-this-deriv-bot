import pino from 'pino';
import pretty from 'pino-pretty';

const stream = pretty({
  colorize: true,
  translateTime: 'SYS:standard',
  ignore: 'pid,hostname',
});

export const logger = pino(
  {
    level: process.env.LOG_LEVEL || 'info',
    base: null,
    redact: {
      paths: ['config.derivApiToken', 'derivApiToken', 'telegramBotToken'],
      censor: '**redacted**',
    },
  },
  stream
);

export type Logger = typeof logger;

