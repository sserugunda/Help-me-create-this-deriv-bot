import TelegramBot from 'node-telegram-bot-api';
import { logger } from '../utils/logger';

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

export class Telegram {
  private bot: TelegramBot;
  private chatId?: string;

  constructor(opts: BotOptions, commands: BotCommands) {
    this.bot = new TelegramBot(opts.token, { polling: true });
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

    this.bot.on('polling_error', (err) => logger.error({ err }, 'Telegram polling error'));
  }

  async send(text: string): Promise<void> {
    if (!this.chatId) return;
    await this.bot.sendMessage(this.chatId, text, { parse_mode: 'Markdown' });
  }

  setChatId(chatId: string | number) {
    this.chatId = String(chatId);
  }

  private ensureChat(chatId: number) {
    if (!this.chatId) this.chatId = String(chatId);
  }
}

