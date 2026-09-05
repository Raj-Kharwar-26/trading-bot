# Momentum Swing Trading Strategy (sample)

## Rules
- Only trade in the direction of the dominant daily trend (EMA-50 slope up = long bias, down = short bias).
- Volume confirmation required: entry-day volume must exceed the 20-day average volume by at least 1.5x.
- Do not chase extended moves: skip a signal if price is more than 3 ATR(14) above the most recent swing low for a long.
- Risk per trade is capped at 1% of equity. Position size = (equity * 0.01) / (entry_price - stop_loss).

## Entry
- Long: close breaks above the prior 20-day high AND EMA-50 slope up AND volume > 1.5x average.
- Stop-loss: below the most recent swing low minus one ATR(14).

## Targets & management
- Initial target: 2R. Take partial profit at 1R (50%), trail the rest.
- Minimum risk:reward is 1.5. If a setup offers less than 1.5R, skip it.
- Kill switch: if daily loss reaches 3% of equity, stop trading for the day.

## Conflits
- Never hold earnings/event risk overnight unless the trade is small and approved.
- BSE instruments require additional data checks before entry.
