import { DerivClient } from './client';
export interface StrategyOptions {
    granularity: number;
    stakeFractionOfBalance: number;
    percentageStep: number;
}
export declare class StrategyEngine {
    private client;
    private options;
    private indicators;
    private lastClosedEpoch;
    private notify;
    constructor(client: DerivClient, options: StrategyOptions, notify?: (text: string) => void | Promise<void>);
    private getIndicator;
    warmup(symbol: string): Promise<void>;
    onOhlc(symbol: string, ohlc: any): Promise<void>;
    private tryRiseTrade;
}
//# sourceMappingURL=strategy.d.ts.map