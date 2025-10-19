"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.BreakoutProbabilityIndicator = void 0;
class BreakoutProbabilityIndicator {
    constructor(options) {
        this.options = options;
        this.counters = {
            greenTotal: 0,
            redTotal: 0,
            g_hit_high: 0,
            g_hit_low: 0,
            r_hit_high: 0,
            r_hit_low: 0,
        };
    }
    // Consume candles in chronological order to build statistics
    feed(candles) {
        for (let i = 1; i < candles.length; i++) {
            const prev = candles[i - 1];
            const curr = candles[i];
            if (!prev || !curr)
                continue;
            this.updateCounters(prev, curr);
        }
    }
    // Update when a new candle closes
    onCandleClose(prev, curr) {
        this.updateCounters(prev, curr);
        return this.getSignal(prev, curr);
    }
    updateCounters(prev, curr) {
        const green = prev.close > prev.open;
        const red = prev.close < prev.open;
        const step = prev.close * (this.options.percentageStep / 100);
        const hi = prev.high + step; // align with Pine using h[1] + step
        const lo = prev.low - step; // and l[1] - step
        const hitHigh = curr.high >= hi;
        const hitLow = curr.low <= lo;
        if (green) {
            this.counters.greenTotal += 1;
            if (hitHigh)
                this.counters.g_hit_high += 1;
            if (hitLow)
                this.counters.g_hit_low += 1;
        }
        else if (red) {
            this.counters.redTotal += 1;
            if (hitHigh)
                this.counters.r_hit_high += 1;
            if (hitLow)
                this.counters.r_hit_low += 1;
        }
    }
    getSignal(prev, curr) {
        const green = prev.close > prev.open;
        const red = prev.close < prev.open;
        // Compute probabilities akin to Pine variables a1,b1,a2,b2
        const gtotal = Math.max(this.counters.greenTotal, 1);
        const rtotal = Math.max(this.counters.redTotal, 1);
        const a1 = this.counters.g_hit_high / gtotal; // probability high hit after green
        const b1 = this.counters.g_hit_low / gtotal; // probability low hit after green
        const a2 = this.counters.r_hit_high / rtotal; // probability high hit after red
        const b2 = this.counters.r_hit_low / rtotal; // probability low hit after red
        let bias = 'NEUTRAL';
        let bull = 0;
        let bear = 0;
        if (green) {
            bias = Math.max(a1, b1) === a1 ? 'BULLISH' : 'BEARISH';
            bull = a1;
            bear = b1;
        }
        else if (red) {
            bias = Math.max(a2, b2) === a2 ? 'BULLISH' : 'BEARISH';
            bull = a2;
            bear = b2;
        }
        return {
            symbol: '', // filled by strategy
            epoch: curr.epoch,
            bias,
            bullishProbability: bull,
            bearishProbability: bear,
        };
    }
}
exports.BreakoutProbabilityIndicator = BreakoutProbabilityIndicator;
//# sourceMappingURL=indicator.js.map