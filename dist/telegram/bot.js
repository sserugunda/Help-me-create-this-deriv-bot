"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.Telegram = void 0;
const node_telegram_bot_api_1 = __importDefault(require("node-telegram-bot-api"));
const logger_1 = require("../utils/logger");
class Telegram {
    constructor(opts, commands) {
        this.bot = new node_telegram_bot_api_1.default(opts.token, { polling: true });
        if (opts.chatId !== undefined) {
            this.chatId = opts.chatId;
        }
        this.bot.onText(/^\/start\b/i, async (msg) => {
            this.ensureChat(msg.chat.id);
            const res = await commands.onStart();
            await this.send(res);
        });
        this.bot.onText(/^\/stop\b/i, async (msg) => {
            this.ensureChat(msg.chat.id);
            const res = await commands.onStop();
            await this.send(res);
        });
        this.bot.onText(/^\/status\b/i, async (msg) => {
            this.ensureChat(msg.chat.id);
            const res = await commands.onStatus();
            await this.send(res);
        });
        this.bot.onText(/^\/stake\s+(\d+(?:\.\d+)?)\b/i, async (msg, match) => {
            this.ensureChat(msg.chat.id);
            const pct = Number(match?.[1] || '1');
            const res = await commands.onSetStakePct(pct);
            await this.send(res);
        });
        this.bot.on('polling_error', (err) => logger_1.logger.error({ err }, 'Telegram polling error'));
    }
    async send(text) {
        if (!this.chatId)
            return;
        await this.bot.sendMessage(this.chatId, text, { parse_mode: 'Markdown' });
    }
    setChatId(chatId) {
        this.chatId = String(chatId);
    }
    ensureChat(chatId) {
        if (!this.chatId)
            this.chatId = String(chatId);
    }
}
exports.Telegram = Telegram;
//# sourceMappingURL=bot.js.map