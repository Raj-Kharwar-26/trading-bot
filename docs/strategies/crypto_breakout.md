# Crypto Breakout & Trend Strategy (BTC/majors)

Trading crypto (24/7) requires different rules than equities: no earnings/event
overnight risk, but higher volatility and gap risk. This complements
`momentum_swing.md`.

## Rules
- Trade only in the direction of the dominant daily trend: EMA-50 slope up =
  long bias, down = short bias. Skip if EMA-50 is flat (slope within +/-0.1%).
- Breakout confirmation required: enter only on a close above the prior 20-day
  high (long) or below the prior 20-day low (short), with entry-day volume above
  the 20-day average (>= 1.2x). Volume above 1.5x is preferred but not mandatory
  for crypto.
- Do not chase: skip a long if price is more than 2.5 ATR(14) above the most
  recent swing low; skip a short if more than 2.5 ATR(14) below the most recent
  swing high.
- Risk per trade cap: 1% of equity. Position size =
  (equity * 0.01) / |entry - stop_loss|. For crypto round position to the
  exchange lot step.

## Entry
- Long: daily close breaks above prior 20-day high AND EMA-50 slope up.
- Short: daily close breaks below prior 20-day low AND EMA-50 slope down.
- Use limit orders near the level; avoid market orders into thin order books.

## Stop-loss
- Long: below the most recent swing low minus 1 ATR(14).
- Short: above the most recent swing high plus 1 ATR(14).

## Targets & management
- Initial target: 2R. Take partial profit at 1R (50%), trail the rest with 1 ATR.
- Minimum reward:risk is 1.5. Skip setups below 1.5R.
- Crypto is 24/7: a position opened near a weekend/volatility spike should be
  sized smaller (0.5x) to absorb gap risk.
- Kill switch: if the total daily loss reaches 3% of equity, stop trading that day.

## Notes
- Reject a signal if the provided facts (EMA/volume/ATR/prior highs-lows) are
  missing - do not invent values.
- Prefer BTC-USD and ETH-USD; altcoins need stricter position sizing given deeper
  drawdowns.