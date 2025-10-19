import { config } from './core/config';
import { parseSymbols } from './core/symbols';
import { logger } from './utils/logger';
import { DerivClient } from './deriv/client';
import { StrategyEngine } from './deriv/strategy';
import { Telegram } from './telegram/bot';

async function main() {
  const symbols = parseSymbols(config.symbolsOverride);
  const client = new DerivClient({ appId: config.derivAppId, apiToken: config.derivApiToken });
  await client.connect();
  await client.authorize();
  await client.subscribeBalance();

  // Telegram first to use for notifications
  const tg = new Telegram(
    { token: config.telegramBotToken, chatId: config.telegramChatId ?? undefined },
    {
      onStart: async () => {
        if (running) return 'Already running';
        running = true;
        if (symbols.length === 0) {
          // If no override, attempt to fetch active symbols and limit to 13 common ones
          const fetched = await client.getActiveVolatilitySymbols(13);
          symbols.push(...fetched);
        }
        await Promise.all(symbols.map((s) => engine.warmup(s)));
        await Promise.all(
          symbols.map((s) =>
            client.subscribeOhlc(s, config.granularity, (ohlc) => {
              if (!running) return;
              engine.onOhlc(s, ohlc);
            })
          )
        );
        return `Started strategy on ${symbols.length} symbols`;
      },
      onStop: async () => {
        running = false;
        return 'Stopped strategy';
      },
      onStatus: async () => {
        const b = client.getBalanceInfo();
        return `Running: ${running}\nBalance: ${b?.balance} ${b?.currency}\nSymbols: ${symbols.join(', ')}`;
      },
      onSetStakePct: async (pct: number) => {
        // mutate runtime stake
        (engine as any).options.stakeFractionOfBalance = pct / 100;
        return `Stake set to ${pct}% of balance`;
      },
    }
  );

  const engine = new StrategyEngine(client, {
    granularity: config.granularity,
    stakeFractionOfBalance: config.stakePct,
    percentageStep: config.percStep,
  }, (text) => tg.send(text));

  let running = false;

  await tg.send('Bot initialized. Use /start to begin.');
}

main().catch((err) => {
  logger.error({ err }, 'Fatal error');
  process.exit(1);
});

