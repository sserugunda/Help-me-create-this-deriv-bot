import type { Candle, StrategySignal } from '../core/types';
export interface IndicatorOptions {
    percentageStep: number;
    levels: number;
    minSamples: number;
}
export declare class BreakoutProbabilityIndicator {
    private options;
    private counters;
    constructor(options: IndicatorOptions);
    feed(candles: Candle[]): void;
    onCandleClose(prev: Candle, curr: Candle): StrategySignal;
    private updateCounters;
    private getSignal;
}
//# sourceMappingURL=indicator.d.ts.map