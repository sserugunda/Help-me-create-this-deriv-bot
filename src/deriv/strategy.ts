import dayjs from 'dayjs';
import { DerivClient } from './client';
import { BreakoutProbabilityIndicator } from './indicator';
import { Candle, StrategySignal } from '../core/types';
import { logger } from '../utils/logger';

export interface StrategyOptions {
  granularity: number; // seconds (60)
  stakeFractionOfBalance: number; // 0..1
  percentageStep: number; // percent
}

export class StrategyEngine {
  private indicators = new Map<string, BreakoutProbabilityIndicator>();
  private lastClosedEpoch = new Map<string, number>();
  private notify: ((text: string) => void | Promise<void>) | undefined;

  constructor(private client: DerivClient, private options: StrategyOptions, notify?: (text: string) => void | Promise<void>) {
    this.notify = notify;
  }

  private getIndicator(symbol: string): BreakoutProbabilityIndicator {
    let ind = this.indicators.get(symbol);
    if (!ind) {
      ind = new BreakoutProbabilityIndicator({ percentageStep: this.options.percentageStep, levels: 5, minSamples: 1 });
      this.indicators.set(symbol, ind);
    }
    return ind;
  }

  async warmup(symbol: string): Promise<void> {
    const gran = this.options.granularity;
    const count = 200; // sufficient to seed stats
    const res = await this.client.getCandlesHistory(symbol, gran, count);
    const candles = (res.candles || []).map((c) => ({
      epoch: c.epoch,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    })) as Candle[];
    if (candles.length >= 2) {
      this.getIndicator(symbol).feed(candles);
      const last = candles[candles.length - 1]!;
      this.lastClosedEpoch.set(symbol, last.epoch);
    }
  }

  async onOhlc(symbol: string, ohlc: any): Promise<void> {
    const isClosed = ohlc.is_closed === 1 || ohlc.is_closed === true || ohlc.epoch === ohlc.close_time; // heuristic
    const epoch: number = Number(ohlc.epoch || ohlc.open_time);
    if (!isClosed) return; // trade only at closure
    const last = this.lastClosedEpoch.get(symbol);
    if (last && epoch <= last) return; // skip duplicates
    this.lastClosedEpoch.set(symbol, epoch);

    // We need previous and current candle. Deriv's ohlc gives the just closed candle.
    // Fetch the previous candle quickly.
    const res = await this.client.getCandlesHistory(symbol, this.options.granularity, 2);
    const cs = (res.candles || []).map((c) => ({ epoch: c.epoch, open: c.open, high: c.high, low: c.low, close: c.close })) as Candle[];
    if (cs.length < 2) return;
    const prev = cs[cs.length - 2]!;
    const curr = cs[cs.length - 1]!;

    const indicator = this.getIndicator(symbol);
    const signal: StrategySignal = indicator.onCandleClose(prev, curr);
    signal.symbol = symbol;

    logger.info({ symbol, epoch: curr.epoch, bias: signal.bias, bull: signal.bullishProbability, bear: signal.bearishProbability }, 'Candle closed');

    if (signal.bias === 'BULLISH' && signal.bullishProbability > signal.bearishProbability) {
      await this.tryRiseTrade(symbol);
    }
  }

  private async tryRiseTrade(symbol: string): Promise<void> {
    const bal = this.client.getBalanceInfo();
    if (!bal) return;
    const stake = Math.max(0.35, Number((bal.balance * this.options.stakeFractionOfBalance).toFixed(2))); // enforce a small minimum
    try {
      const proposal = await this.client.proposalRise(symbol, stake, 1); // 1 minute duration
      const buy = await this.client.buy(proposal.proposal_id, proposal.ask_price);
      logger.info({ symbol, stake, contract_id: buy.contract_id }, 'Bought RISE 1m');
      await this.notify?.(`Bought RISE 1m on ${symbol} | stake ${stake}`);
      await this.client.subscribeOpenContract(buy.contract_id, (u) => {
        if (u.status === 'sold') {
          logger.info({ symbol, payout: u.payout, buy_price: u.buy_price, profit: u.profit }, 'Contract settled');
          this.notify?.(`Settled ${symbol} | payout ${u.payout} | profit ${u.profit}`);
        }
      });
    } catch (err: any) {
      logger.error({ err: err?.message || String(err), symbol }, 'Trade failed');
      await this.notify?.(`Trade failed on ${symbol}: ${err?.message || String(err)}`);
    }
  }
}

