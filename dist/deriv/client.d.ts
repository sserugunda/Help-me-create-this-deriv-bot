type Json = Record<string, any>;
type Ohlc = {
    open: string;
    high: string;
    low: string;
    close: string;
    open_time: number;
    granularity?: number;
    symbol?: string;
    [k: string]: any;
};
type CandleHistoryResponse = {
    candles?: Array<{
        open: number;
        high: number;
        low: number;
        close: number;
        epoch: number;
    }>;
    ohlc?: Ohlc;
    subscription?: {
        id: string;
    };
    echo_req?: Json;
    msg_type: string;
    req_id?: number;
    error?: {
        code: string;
        message: string;
    };
};
export interface DerivClientOptions {
    appId: string;
    apiToken: string;
    endpoint?: string;
}
export interface BalanceInfo {
    balance: number;
    currency: string;
}
export declare class DerivClient {
    private options;
    private ws?;
    private nextReqId;
    private resolvers;
    private subscriptions;
    private onOhlcHandlers;
    private onContractHandlers;
    private authorized;
    private balanceInfo;
    private currency;
    constructor(options: DerivClientOptions);
    connect(): Promise<void>;
    private onMessage;
    private send;
    authorize(): Promise<void>;
    subscribeBalance(onUpdate?: (b: BalanceInfo) => void): Promise<void>;
    getBalanceInfo(): BalanceInfo | null;
    getCurrency(): string | null;
    getCandlesHistory(symbol: string, granularity: number, count: number): Promise<CandleHistoryResponse>;
    getActiveVolatilitySymbols(limit?: number): Promise<string[]>;
    subscribeOhlc(symbol: string, granularity: number, onOhlc: (ohlc: Ohlc) => void): Promise<void>;
    proposalRise(symbol: string, amount: number, durationMinutes: number): Promise<{
        proposal_id: string;
        ask_price: number;
    }>;
    buy(proposal_id: string, price: number): Promise<{
        contract_id: number;
    }>;
    subscribeOpenContract(contract_id: number, onUpdate: (u: any) => void): Promise<void>;
}
export {};
//# sourceMappingURL=client.d.ts.map