const DerivAPI = require('@deriv/deriv-api/dist/DerivAPI');
const WebSocket = require('ws');
const TelegramBot = require('node-telegram-bot-api');
const fs = require('fs');
const path = require('path');

class DerivTradingBot {
  constructor(config) {
    this.derivAppId = config.derivAppId;
    this.derivToken = config.derivToken;
    this.telegramToken = config.telegramToken;
    this.telegramChatId = config.telegramChatId;

    this.api = null;
    this.telegram = null;
    this.connection = null;

    this.symbols = ['1HZ10V', 'R_10', '1HZ15V', 'R_25', '1HZ25V', '1HZ30V', '1HZ50V', 'R_50', '1HZ75V', 'R_75', '1HZ90V', '1HZ100V', 'R_100'];
    this.currentSymbol = 'R_100';

    this.candles5m = {};
    this.candles1m = {};
    this.lastTradeTime = {};
    this.activeTrade = null;
    this.tradeInProgress = false;
    this.alertSent = false;

    this.stats = this.loadStats();
  }

  loadStats() {
    const statsPath = path.join(__dirname, 'stats.json');
    if (fs.existsSync(statsPath)) {
      return JSON.parse(fs.readFileSync(statsPath, 'utf8'));
    }
    return { wins: 0, losses: 0, total: 0 };
  }

  saveStats() {
    const statsPath = path.join(__dirname, 'stats.json');
    fs.writeFileSync(statsPath, JSON.stringify(this.stats, null, 2));
  }

  async initialize() {
    try {
      this.connection = new WebSocket(`wss://ws.binaryws.com/websockets/v3?app_id=${this.derivAppId}`);
      this.api = new DerivAPI({ connection: this.connection });

      this.telegram = new TelegramBot(this.telegramToken, { polling: false });

      await this.authorize();
      await this.sendTelegramMessage('🤖 Bot started and connected to Deriv API');

      console.log('Bot initialized successfully');
    } catch (error) {
      console.error('Initialization error:', error);
      throw error;
    }
  }

  async authorize() {
    const authResponse = await this.api.authorize({ authorize: this.derivToken });
    console.log('Authorized:', authResponse.authorize.loginid);
    return authResponse;
  }

  async getBalance() {
    const balanceResponse = await this.api.balance();
    return parseFloat(balanceResponse.balance.balance);
  }

  async sendTelegramMessage(message) {
    if (this.telegram && this.telegramChatId) {
      try {
        await this.telegram.sendMessage(this.telegramChatId, message, { parse_mode: 'HTML' });
      } catch (error) {
        console.error('Telegram error:', error.message);
      }
    }
  }

  async subscribeToTicks() {
    const tickStream = await this.api.subscribe({ ticks: this.currentSymbol });

    for await (const tick of tickStream) {
      await this.processTick(tick);
    }
  }

  async subscribeToCandles() {
    for (const symbol of this.symbols) {
      this.candles5m[symbol] = [];
      this.candles1m[symbol] = [];
      this.lastTradeTime[symbol] = 0;
    }

    const candles5mStream = await this.api.subscribe({
      ticks_history: this.currentSymbol,
      adjust_start_time: 1,
      count: 10,
      end: 'latest',
      start: Math.floor(Date.now() / 1000) - 3600,
      style: 'candles',
      granularity: 300
    });

    const candles1mStream = await this.api.subscribe({
      ticks_history: this.currentSymbol,
      adjust_start_time: 1,
      count: 10,
      end: 'latest',
      start: Math.floor(Date.now() / 1000) - 3600,
      style: 'candles',
      granularity: 60
    });

    this.processCandles5m(candles5mStream);
    this.processCandles1m(candles1mStream);
  }

  async processCandles5m(stream) {
    for await (const candle of stream) {
      if (candle.candles) {
        this.candles5m[this.currentSymbol] = candle.candles;
      } else if (candle.ohlc) {
        const ohlc = candle.ohlc;
        this.candles5m[this.currentSymbol].push({
          epoch: ohlc.epoch,
          open: parseFloat(ohlc.open),
          high: parseFloat(ohlc.high),
          low: parseFloat(ohlc.low),
          close: parseFloat(ohlc.close)
        });

        if (this.candles5m[this.currentSymbol].length > 10) {
          this.candles5m[this.currentSymbol].shift();
        }

        this.alertSent = false;
        console.log('5m candle closed:', new Date(ohlc.epoch * 1000).toISOString());
      }
    }
  }

  async processCandles1m(stream) {
    for await (const candle of stream) {
      if (candle.candles) {
        this.candles1m[this.currentSymbol] = candle.candles;
      } else if (candle.ohlc) {
        const ohlc = candle.ohlc;
        const candleData = {
          epoch: ohlc.epoch,
          open: parseFloat(ohlc.open),
          high: parseFloat(ohlc.high),
          low: parseFloat(ohlc.low),
          close: parseFloat(ohlc.close)
        };

        this.candles1m[this.currentSymbol].push(candleData);

        if (this.candles1m[this.currentSymbol].length > 10) {
          this.candles1m[this.currentSymbol].shift();
        }

        const candleStartTime = ohlc.epoch;
        const currentTime = Date.now() / 1000;
        const timeIntoCandle = currentTime - candleStartTime;

        if (timeIntoCandle < 10 && !this.alertSent && !this.tradeInProgress) {
          await this.sendGetReadyAlert();
          this.alertSent = true;
        }

        await this.checkSignal();
      }
    }
  }

  async sendGetReadyAlert() {
    if (this.candles5m[this.currentSymbol].length < 1) return;

    const last5mCandle = this.candles5m[this.currentSymbol][this.candles5m[this.currentSymbol].length - 1];
    const isBullish = last5mCandle.close > last5mCandle.open;
    const isBearish = last5mCandle.close < last5mCandle.open;

    if (isBullish || isBearish) {
      const direction = isBullish ? '📈 RISE' : '📉 FALL';
      await this.sendTelegramMessage(
        `⚠️ <b>GET READY!</b>\n\n` +
        `Symbol: ${this.currentSymbol}\n` +
        `Monitoring 1-minute candle for ${direction} signal...\n` +
        `Time: ${new Date().toLocaleTimeString()}`
      );
    }
  }

  async checkSignal() {
    if (this.tradeInProgress) return;
    if (this.candles5m[this.currentSymbol].length < 1) return;
    if (this.candles1m[this.currentSymbol].length < 1) return;

    const last5mCandle = this.candles5m[this.currentSymbol][this.candles5m[this.currentSymbol].length - 1];
    const last1mCandle = this.candles1m[this.currentSymbol][this.candles1m[this.currentSymbol].length - 1];

    const timeSinceLastTrade = (Date.now() / 1000) - this.lastTradeTime[this.currentSymbol];
    if (timeSinceLastTrade < 300) return;

    if (last1mCandle.epoch <= last5mCandle.epoch) return;

    const signal = this.evaluateStrategy(last5mCandle, last1mCandle);

    if (signal) {
      await this.executeSignal(signal, last5mCandle, last1mCandle);
    }
  }

  evaluateStrategy(candle5m, candle1m) {
    const midpoint5m = (candle5m.open + candle5m.close) / 2;

    const is5mBullish = candle5m.close > candle5m.open;
    const is5mBearish = candle5m.close < candle5m.open;

    const is1mBullish = candle1m.close > candle1m.open;
    const is1mBearish = candle1m.close < candle1m.open;

    if (is5mBullish && is1mBullish) {
      const candle1mHigh = Math.max(candle1m.open, candle1m.close);
      const candle1mLow = Math.min(candle1m.open, candle1m.close);

      if (candle1mHigh < midpoint5m) {
        return {
          type: 'CALL',
          direction: '📈 RISE',
          midpoint: midpoint5m,
          candle1mHigh,
          candle1mLow
        };
      }
    }

    if (is5mBearish && is1mBearish) {
      const candle1mHigh = Math.max(candle1m.open, candle1m.close);
      const candle1mLow = Math.min(candle1m.open, candle1m.close);

      if (candle1mLow > midpoint5m) {
        return {
          type: 'PUT',
          direction: '📉 FALL',
          midpoint: midpoint5m,
          candle1mHigh,
          candle1mLow
        };
      }
    }

    return null;
  }

  async executeSignal(signal, candle5m, candle1m) {
    this.lastTradeTime[this.currentSymbol] = Date.now() / 1000;
    this.tradeInProgress = true;

    const message =
      `🎯 <b>TRADE SIGNAL</b>\n\n` +
      `${signal.direction}\n` +
      `Symbol: ${this.currentSymbol}\n` +
      `Duration: 3 minutes\n\n` +
      `5m Candle Midpoint: ${signal.midpoint.toFixed(5)}\n` +
      `1m Candle Range: ${signal.candle1mLow.toFixed(5)} - ${signal.candle1mHigh.toFixed(5)}\n\n` +
      `Time: ${new Date().toLocaleTimeString()}`;

    await this.sendTelegramMessage(message);

    try {
      const balance = await this.getBalance();
      const stake = balance * 0.10;

      const proposal = await this.api.proposal({
        proposal: 1,
        amount: stake.toFixed(2),
        basis: 'stake',
        contract_type: signal.type,
        currency: 'USD',
        duration: 3,
        duration_unit: 'm',
        symbol: this.currentSymbol
      });

      if (proposal.error) {
        await this.sendTelegramMessage(`❌ Proposal error: ${proposal.error.message}`);
        this.tradeInProgress = false;
        return;
      }

      const buyResponse = await this.api.buy({
        buy: proposal.proposal.id,
        price: stake.toFixed(2)
      });

      if (buyResponse.error) {
        await this.sendTelegramMessage(`❌ Buy error: ${buyResponse.error.message}`);
        this.tradeInProgress = false;
        return;
      }

      await this.sendTelegramMessage(
        `✅ <b>Trade Executed</b>\n\n` +
        `Contract ID: ${buyResponse.buy.contract_id}\n` +
        `Stake: $${stake.toFixed(2)}\n` +
        `Potential Payout: $${buyResponse.buy.payout.toFixed(2)}`
      );

      this.activeTrade = {
        contractId: buyResponse.buy.contract_id,
        stake: stake,
        payout: buyResponse.buy.payout,
        type: signal.type
      };

      await this.monitorTrade(buyResponse.buy.contract_id);

    } catch (error) {
      console.error('Trade execution error:', error);
      await this.sendTelegramMessage(`❌ Trade error: ${error.message}`);
      this.tradeInProgress = false;
    }
  }

  async monitorTrade(contractId) {
    try {
      const proposalOpenContract = await this.api.proposalOpenContract({
        proposal_open_contract: 1,
        contract_id: contractId,
        subscribe: 1
      });

      for await (const contract of proposalOpenContract) {
        if (contract.proposal_open_contract) {
          const poc = contract.proposal_open_contract;

          if (poc.is_sold || poc.status === 'sold') {
            const profit = parseFloat(poc.profit);
            const isWin = profit > 0;

            if (isWin) {
              this.stats.wins++;
            } else {
              this.stats.losses++;
            }
            this.stats.total++;
            this.saveStats();

            const winRate = ((this.stats.wins / this.stats.total) * 100).toFixed(2);

            const resultMessage =
              `${isWin ? '✅ WIN' : '❌ LOSS'}\n\n` +
              `Profit/Loss: $${profit.toFixed(2)}\n` +
              `Final Price: ${poc.exit_tick_display_value}\n\n` +
              `📊 <b>Statistics</b>\n` +
              `Wins: ${this.stats.wins}\n` +
              `Losses: ${this.stats.losses}\n` +
              `Total Trades: ${this.stats.total}\n` +
              `Win Rate: ${winRate}%`;

            await this.sendTelegramMessage(resultMessage);

            this.tradeInProgress = false;
            this.activeTrade = null;
            break;
          }
        }
      }
    } catch (error) {
      console.error('Trade monitoring error:', error);
      this.tradeInProgress = false;
    }
  }

  async start() {
    await this.initialize();
    await this.subscribeToCandles();
  }
}

module.exports = DerivTradingBot;
