import WebSocket from 'ws';
import { logger } from '../utils/logger';

type Json = Record<string, any>;

type Ohlc = {
  open: string;
  high: string;
  low: string;
  close: string;
  open_time: number; // epoch seconds
  granularity?: number;
  symbol?: string;
  // Deriv sometimes provides additional flags like "is_closed" or similar; optional here
  [k: string]: any;
};

type CandleHistoryResponse = {
  candles?: Array<{
    open: number;
    high: number;
    low: number;
    close: number;
    epoch: number;
  }>; // returned when requesting static history
  ohlc?: Ohlc; // returned on streaming updates
  subscription?: { id: string };
  echo_req?: Json;
  msg_type: string;
  req_id?: number;
  error?: { code: string; message: string };
};

export interface DerivClientOptions {
  appId: string;
  apiToken: string;
  endpoint?: string; // allow override for testing
}

export interface BalanceInfo {
  balance: number;
  currency: string;
}

type Resolver = {
  resolve: (data: any) => void;
  reject: (err: any) => void;
};

export class DerivClient {
  private ws?: WebSocket;
  private nextReqId = 1;
  private resolvers = new Map<number, Resolver>();
  private subscriptions = new Map<string, (data: any) => void>();
  private onOhlcHandlers = new Map<string, (ohlc: Ohlc) => void>();
  private onContractHandlers = new Map<number, (update: any) => void>();
  private authorized = false;
  private balanceInfo: BalanceInfo | null = null;
  private currency: string | null = null;

  constructor(private options: DerivClientOptions) {}

  async connect(): Promise<void> {
    const url = (this.options.endpoint || 'wss://ws.derivws.com/websockets/v3') + `?app_id=${this.options.appId}`;
    logger.info({ url }, 'Connecting to Deriv WS');
    this.ws = new WebSocket(url);
    await new Promise<void>((resolve, reject) => {
      if (!this.ws) return reject(new Error('ws is undefined'));
      this.ws.on('open', () => resolve());
      this.ws.on('error', (err) => reject(err));
    });

    this.ws.on('message', (buf: WebSocket.RawData) => this.onMessage(buf));
    this.ws.on('close', (code, reason) => {
      logger.warn({ code, reason: reason.toString() }, 'Deriv WS closed');
    });
  }

  private onMessage(buf: WebSocket.RawData) {
    const msg = JSON.parse(buf.toString());
    const { msg_type, req_id, error } = msg;
    if (error) {
      logger.error({ error, msg_type }, 'Deriv error message');
      if (req_id && this.resolvers.has(req_id)) {
        this.resolvers.get(req_id)!.reject(new Error(error.message));
        this.resolvers.delete(req_id);
      }
      return;
    }

    // route subscription messages
    if (msg.subscription && msg.subscription.id) {
      const subId = msg.subscription.id as string;
      const handler = this.subscriptions.get(subId);
      if (handler) handler(msg);
    }

    // route standard responses
    if (req_id && this.resolvers.has(req_id)) {
      const { resolve } = this.resolvers.get(req_id)!;
      this.resolvers.delete(req_id);
      resolve(msg);
      return;
    }

    // convenience routing for ohlc / open contract
    if (msg_type === 'ohlc') {
      const ohlc: Ohlc = msg.ohlc;
      if (ohlc && ohlc.symbol) {
        const handler = this.onOhlcHandlers.get(ohlc.symbol);
        if (handler) handler(ohlc);
      }
    } else if (msg_type === 'proposal_open_contract') {
      const poc = msg.proposal_open_contract;
      const contractId = poc?.contract_id as number | undefined;
      if (contractId != null) {
        const handler = this.onContractHandlers.get(contractId);
        if (handler) handler(poc);
      }
    }
  }

  private send<T = any>(payload: Json): Promise<T> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error('WebSocket not connected'));
    }
    const req_id = this.nextReqId++;
    const body = { ...payload, req_id };
    this.ws.send(JSON.stringify(body));
    return new Promise<T>((resolve, reject) => {
      this.resolvers.set(req_id, { resolve, reject });
      setTimeout(() => {
        if (this.resolvers.has(req_id)) {
          this.resolvers.get(req_id)!.reject(new Error(`Request ${req_id} timeout`));
          this.resolvers.delete(req_id);
        }
      }, 15000);
    });
  }

  async authorize(): Promise<void> {
    const res = await this.send({ authorize: this.options.apiToken });
    this.authorized = true;
    this.currency = res.authorize?.currency || null;
    logger.info({ loginid: res.authorize?.loginid, currency: this.currency }, 'Authorized');
  }

  async subscribeBalance(onUpdate?: (b: BalanceInfo) => void): Promise<void> {
    const res = await this.send<{ subscription: { id: string } }>({ balance: 1, subscribe: 1 });
    const subId = (res as any).subscription?.id as string | undefined;
    if (subId) {
      this.subscriptions.set(subId, (msg) => {
        const balance = msg.balance?.balance as number | undefined;
        const currency = msg.balance?.currency as string | undefined;
        if (typeof balance === 'number' && currency) {
          this.balanceInfo = { balance, currency };
          onUpdate?.(this.balanceInfo);
        }
      });
    }
  }

  getBalanceInfo(): BalanceInfo | null {
    return this.balanceInfo;
  }

  getCurrency(): string | null {
    return this.currency || this.balanceInfo?.currency || null;
  }

  async getCandlesHistory(symbol: string, granularity: number, count: number): Promise<CandleHistoryResponse> {
    const res = await this.send<CandleHistoryResponse>({
      ticks_history: symbol,
      style: 'candles',
      granularity,
      count,
    });
    return res;
  }

  async getActiveVolatilitySymbols(limit?: number): Promise<string[]> {
    const res = await this.send({ active_symbols: 'brief', product_type: 'basic' });
    const all = (res.active_symbols || []) as Array<any>;
    const vols = all
      .filter((s) =>
        (s.market === 'synthetic_index' || /synthetic/i.test(s.market_display_name || '')) &&
        (s.symbol_type === 'random_index' || /volatility/i.test(s.display_name || ''))
      )
      .filter((s) => /^R_/.test(s.symbol)) // avoid non-random symbols like BOOM/CRASH/JUMP
      .sort((a, b) => String(a.display_name).localeCompare(String(b.display_name)))
      .map((s) => s.symbol as string);
    if (limit && vols.length > limit) return vols.slice(0, limit);
    return vols;
  }

  async subscribeOhlc(
    symbol: string,
    granularity: number,
    onOhlc: (ohlc: Ohlc) => void
  ): Promise<void> {
    const res = await this.send<CandleHistoryResponse>({
      ticks_history: symbol,
      style: 'candles',
      granularity,
      subscribe: 1,
    });
    const subId = res.subscription?.id;
    if (subId) {
      this.subscriptions.set(subId, (msg: CandleHistoryResponse) => {
        if (msg.ohlc) {
          // Note: Deriv may not include the symbol inside ohlc; attach for routing
          const withSymbol = { ...msg.ohlc, symbol } as Ohlc;
          const handler = this.onOhlcHandlers.get(symbol);
          if (handler) handler(withSymbol);
        }
      });
    }
    this.onOhlcHandlers.set(symbol, onOhlc);
  }

  async proposalRise(symbol: string, amount: number, durationMinutes: number): Promise<{ proposal_id: string; ask_price: number }> {
    const currency = this.getCurrency();
    if (!currency) throw new Error('Unknown account currency');
    const res = await this.send({
      proposal: 1,
      amount: Number(amount.toFixed(2)),
      basis: 'stake',
      contract_type: 'CALL',
      currency,
      duration: durationMinutes,
      duration_unit: 'm',
      symbol,
    });
    const p = res.proposal;
    if (!p) throw new Error('No proposal returned');
    return { proposal_id: p.id, ask_price: Number(p.ask_price) };
  }

  async buy(proposal_id: string, price: number): Promise<{ contract_id: number }> {
    const res = await this.send({ buy: proposal_id, price });
    const buy = res.buy;
    if (!buy) throw new Error('No buy result');
    return { contract_id: buy.contract_id };
  }

  async subscribeOpenContract(contract_id: number, onUpdate: (u: any) => void): Promise<void> {
    const res = await this.send({ proposal_open_contract: 1, contract_id, subscribe: 1 });
    const id = res.subscription?.id as string | undefined;
    if (id) {
      this.subscriptions.set(id, (msg) => onUpdate(msg.proposal_open_contract));
      this.onContractHandlers.set(contract_id, onUpdate);
    }
  }
}

