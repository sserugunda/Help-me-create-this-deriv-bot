export interface BotOptions {
    token: string;
    chatId?: string | undefined;
}
export type BotCommands = {
    onStart: () => Promise<string> | string;
    onStop: () => Promise<string> | string;
    onStatus: () => Promise<string> | string;
    onSetStakePct: (pct: number) => Promise<string> | string;
};
export declare class Telegram {
    private bot;
    private chatId?;
    constructor(opts: BotOptions, commands: BotCommands);
    send(text: string): Promise<void>;
    setChatId(chatId: string | number): void;
    private ensureChat;
}
//# sourceMappingURL=bot.d.ts.map