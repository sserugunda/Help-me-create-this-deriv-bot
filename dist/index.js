"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
const config_1 = require("./core/config");
const symbols_1 = require("./core/symbols");
const logger_1 = require("./utils/logger");
const client_1 = require("./deriv/client");
const strategy_1 = require("./deriv/strategy");
const bot_1 = require("./telegram/bot");
async function main() {
    const symbols = (0, symbols_1.parseSymbols)(config_1.config.symbolsOverride);
    const client = new client_1.DerivClient({ appId: config_1.config.derivAppId, apiToken: config_1.config.derivApiToken });
    await client.connect();
    await client.authorize();
    await client.subscribeBalance();
    // Telegram first to use for notifications
    const tg = new bot_1.Telegram({ token: config_1.config.telegramBotToken, chatId: config_1.config.telegramChatId ?? undefined }, {
        onStart: async () => {
            if (running)
                return 'Already running';
            running = true;
            if (symbols.length === 0) {
                // If no override, attempt to fetch active symbols and limit to 13 common ones
                const fetched = await client.getActiveVolatilitySymbols(13);
                symbols.push(...fetched);
            }
            await Promise.all(symbols.map((s) => engine.warmup(s)));
            await Promise.all(symbols.map((s) => client.subscribeOhlc(s, config_1.config.granularity, (ohlc) => {
                if (!running)
                    return;
                engine.onOhlc(s, ohlc);
            })));
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
        onSetStakePct: async (pct) => {
            // mutate runtime stake
            engine.options.stakeFractionOfBalance = pct / 100;
            return `Stake set to ${pct}% of balance`;
        },
    });
    const engine = new strategy_1.StrategyEngine(client, {
        granularity: config_1.config.granularity,
        stakeFractionOfBalance: config_1.config.stakePct,
        percentageStep: config_1.config.percStep,
    }, (text) => tg.send(text));
    let running = false;
    await tg.send('Bot initialized. Use /start to begin.');
}
main().catch((err) => {
    logger_1.logger.error({ err }, 'Fatal error');
    process.exit(1);
});
//# sourceMappingURL=index.js.map