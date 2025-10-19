"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.DerivClient = void 0;
const ws_1 = __importDefault(require("ws"));
const logger_1 = require("../utils/logger");
class DerivClient {
    constructor(options) {
        this.options = options;
        this.nextReqId = 1;
        this.resolvers = new Map();
        this.subscriptions = new Map();
        this.onOhlcHandlers = new Map();
        this.onContractHandlers = new Map();
        this.authorized = false;
        this.balanceInfo = null;
        this.currency = null;
    }
    async connect() {
        const url = (this.options.endpoint || 'wss://ws.derivws.com/websockets/v3') + `?app_id=${this.options.appId}`;
        logger_1.logger.info({ url }, 'Connecting to Deriv WS');
        this.ws = new ws_1.default(url);
        await new Promise((resolve, reject) => {
            if (!this.ws)
                return reject(new Error('ws is undefined'));
            this.ws.on('open', () => resolve());
            this.ws.on('error', (err) => reject(err));
        });
        this.ws.on('message', (buf) => this.onMessage(buf));
        this.ws.on('close', (code, reason) => {
            logger_1.logger.warn({ code, reason: reason.toString() }, 'Deriv WS closed');
        });
    }
    onMessage(buf) {
        const msg = JSON.parse(buf.toString());
        const { msg_type, req_id, error } = msg;
        if (error) {
            logger_1.logger.error({ error, msg_type }, 'Deriv error message');
            if (req_id && this.resolvers.has(req_id)) {
                this.resolvers.get(req_id).reject(new Error(error.message));
                this.resolvers.delete(req_id);
            }
            return;
        }
        // route subscription messages
        if (msg.subscription && msg.subscription.id) {
            const subId = msg.subscription.id;
            const handler = this.subscriptions.get(subId);
            if (handler)
                handler(msg);
        }
        // route standard responses
        if (req_id && this.resolvers.has(req_id)) {
            const { resolve } = this.resolvers.get(req_id);
            this.resolvers.delete(req_id);
            resolve(msg);
            return;
        }
        // convenience routing for ohlc / open contract
        if (msg_type === 'ohlc') {
            const ohlc = msg.ohlc;
            if (ohlc && ohlc.symbol) {
                const handler = this.onOhlcHandlers.get(ohlc.symbol);
                if (handler)
                    handler(ohlc);
            }
        }
        else if (msg_type === 'proposal_open_contract') {
            const poc = msg.proposal_open_contract;
            const contractId = poc?.contract_id;
            if (contractId != null) {
                const handler = this.onContractHandlers.get(contractId);
                if (handler)
                    handler(poc);
            }
        }
    }
    send(payload) {
        if (!this.ws || this.ws.readyState !== ws_1.default.OPEN) {
            return Promise.reject(new Error('WebSocket not connected'));
        }
        const req_id = this.nextReqId++;
        const body = { ...payload, req_id };
        this.ws.send(JSON.stringify(body));
        return new Promise((resolve, reject) => {
            this.resolvers.set(req_id, { resolve, reject });
            setTimeout(() => {
                if (this.resolvers.has(req_id)) {
                    this.resolvers.get(req_id).reject(new Error(`Request ${req_id} timeout`));
                    this.resolvers.delete(req_id);
                }
            }, 15000);
        });
    }
    async authorize() {
        const res = await this.send({ authorize: this.options.apiToken });
        this.authorized = true;
        this.currency = res.authorize?.currency || null;
        logger_1.logger.info({ loginid: res.authorize?.loginid, currency: this.currency }, 'Authorized');
    }
    async subscribeBalance(onUpdate) {
        const res = await this.send({ balance: 1, subscribe: 1 });
        const subId = res.subscription?.id;
        if (subId) {
            this.subscriptions.set(subId, (msg) => {
                const balance = msg.balance?.balance;
                const currency = msg.balance?.currency;
                if (typeof balance === 'number' && currency) {
                    this.balanceInfo = { balance, currency };
                    onUpdate?.(this.balanceInfo);
                }
            });
        }
    }
    getBalanceInfo() {
        return this.balanceInfo;
    }
    getCurrency() {
        return this.currency || this.balanceInfo?.currency || null;
    }
    async getCandlesHistory(symbol, granularity, count) {
        const res = await this.send({
            ticks_history: symbol,
            style: 'candles',
            granularity,
            count,
        });
        return res;
    }
    async getActiveVolatilitySymbols(limit) {
        const res = await this.send({ active_symbols: 'brief', product_type: 'basic' });
        const all = (res.active_symbols || []);
        const vols = all
            .filter((s) => (s.market === 'synthetic_index' || /synthetic/i.test(s.market_display_name || '')) &&
            (s.symbol_type === 'random_index' || /volatility/i.test(s.display_name || '')))
            .filter((s) => /^R_/.test(s.symbol)) // avoid non-random symbols like BOOM/CRASH/JUMP
            .sort((a, b) => String(a.display_name).localeCompare(String(b.display_name)))
            .map((s) => s.symbol);
        if (limit && vols.length > limit)
            return vols.slice(0, limit);
        return vols;
    }
    async subscribeOhlc(symbol, granularity, onOhlc) {
        const res = await this.send({
            ticks_history: symbol,
            style: 'candles',
            granularity,
            subscribe: 1,
        });
        const subId = res.subscription?.id;
        if (subId) {
            this.subscriptions.set(subId, (msg) => {
                if (msg.ohlc) {
                    // Note: Deriv may not include the symbol inside ohlc; attach for routing
                    const withSymbol = { ...msg.ohlc, symbol };
                    const handler = this.onOhlcHandlers.get(symbol);
                    if (handler)
                        handler(withSymbol);
                }
            });
        }
        this.onOhlcHandlers.set(symbol, onOhlc);
    }
    async proposalRise(symbol, amount, durationMinutes) {
        const currency = this.getCurrency();
        if (!currency)
            throw new Error('Unknown account currency');
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
        if (!p)
            throw new Error('No proposal returned');
        return { proposal_id: p.id, ask_price: Number(p.ask_price) };
    }
    async buy(proposal_id, price) {
        const res = await this.send({ buy: proposal_id, price });
        const buy = res.buy;
        if (!buy)
            throw new Error('No buy result');
        return { contract_id: buy.contract_id };
    }
    async subscribeOpenContract(contract_id, onUpdate) {
        const res = await this.send({ proposal_open_contract: 1, contract_id, subscribe: 1 });
        const id = res.subscription?.id;
        if (id) {
            this.subscriptions.set(id, (msg) => onUpdate(msg.proposal_open_contract));
            this.onContractHandlers.set(contract_id, onUpdate);
        }
    }
}
exports.DerivClient = DerivClient;
//# sourceMappingURL=client.js.map