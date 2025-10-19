export type VolatilitySymbol = 'R_10' | 'R_25' | 'R_50' | 'R_75' | 'R_100' | 'RV_10' | 'RV_25' | 'RV_50' | 'RV_75' | 'RV_100' | 'RDBEAR' | 'RDBULL' | 'BOOM_1000';
export type Candle = {
    epoch: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume?: number;
};
export type Bias = 'BULLISH' | 'BEARISH' | 'NEUTRAL';
export interface StrategySignal {
    symbol: string;
    epoch: number;
    bias: Bias;
    bullishProbability: number;
    bearishProbability: number;
}
export interface TradeOrder {
    symbol: string;
    contractType: 'RISE' | 'FALL';
    duration: number;
    stake: number;
}
//# sourceMappingURL=types.d.ts.map