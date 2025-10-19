import 'dotenv/config';
export declare const config: {
    readonly derivAppId: string;
    readonly derivApiToken: string;
    readonly telegramBotToken: string;
    readonly telegramChatId: string | undefined;
    readonly symbolsOverride: string | undefined;
    readonly stakePct: number;
    readonly granularity: number;
    readonly percStep: number;
    readonly minSamples: number;
};
export type AppConfig = typeof config;
//# sourceMappingURL=config.d.ts.map