# Backtest Perp Desk (Hyperliquid, dati reali)

Universo: BTC, ETH, HYPE, ZEC, SOL, BNB, kPEPE, NEAR, ENA, XRP, XMR, UNI, PUMP, LIT, TRX, WLD, AAVE, LINK (le 18 perp con più volume 24h oggi).
TRAIN 2026-09-11 12:30 → 2026-10-11 03:30 · TEST (fuori campione) 2026-10-11 03:30 → 2026-10-30 20:30 (ora di Roma).
BTC nel periodo: train +7.8%, test -7.74%.

Costi inclusi: fee taker 0,045% per lato, slippage stimato dal book L2 reale per $3k (min 0,01% per lato, raddoppiato sugli stop), funding orario storico, margine isolato con liquidazione. Rischio 1% per trade, max 4 posizioni, esposizione max 3x, stop giornaliero −5%, guardia di correlazione. Ordine conservativo dentro la candela (stop prima del TP).

| Variante | Train trade | Train rend. | Train maxDD | Train PF | Test trade | Test rend. | Test maxDD | Test PF |
|---|---|---|---|---|---|---|---|---|
| {'breakout_tf': '15m', 'donchian_n': 20, 'sl_atr': 1.5, 'trail_atr': 2.5} | 207 | -9.66% | -31.25% | 0.92 | 122 | +8.29% | -18.31% | 1.13 |
| {'breakout_tf': '15m', 'donchian_n': 40, 'sl_atr': 1.5, 'trail_atr': 2.5} | 194 | +4.26% | -25.63% | 1.04 | 119 | -1.61% | -17.73% | 0.97 |
| {'breakout_tf': '15m', 'donchian_n': 40, 'sl_atr': 2.0, 'trail_atr': 3.0} | 151 | -4.2% | -19.56% | 0.95 | 98 | -12.41% | -19.35% | 0.76 |
| {'breakout_tf': '15m', 'donchian_n': 40, 'sl_atr': 1.5, 'trail_atr': 2.5, 'trend_min_atr': 1.0} | 158 | +4.71% | -18.79% | 1.05 | 103 | -7.27% | -20.62% | 0.87 |
| {'breakout_tf': '1h', 'donchian_n_1h': 24, 'sl_atr': 1.5, 'trail_atr': 2.5} | 133 | -11.72% | -26.16% | 0.85 | 76 | -16.64% | -23.9% | 0.62 |
| {'breakout_tf': '1h', 'donchian_n_1h': 24, 'sl_atr': 2.0, 'trail_atr': 3.0} | 116 | -10.68% | -19.78% | 0.84 | 64 | -13.03% | -20.83% | 0.64 |
| {'breakout_tf': '1h', 'donchian_n_1h': 48, 'sl_atr': 2.0, 'trail_atr': 3.0} | 103 | -27.24% | -30.04% | 0.53 | 48 | -10.8% | -18.41% | 0.58 |
| {'breakout_tf': '1h', 'donchian_n_1h': 24, 'sl_atr': 2.0, 'trail_atr': 3.0, 'trend_min_atr': 1.0} | 94 | -8.44% | -13.18% | 0.83 | 52 | -10.96% | -21.92% | 0.63 |

**Scelta solo sul train** (rendimento/drawdown, ≥15 trade): {'breakout_tf': '15m', 'donchian_n': 40, 'sl_atr': 1.5, 'trail_atr': 2.5, 'trend_min_atr': 1.0}
- Train: {'trades': 158, 'win_rate': 41.8, 'return_pct': 4.71, 'max_dd_pct': -18.79, 'profit_factor': 1.05, 'avg_r': 0.043, 'median_r': -0.46, 'fees_usd': 82.95, 'funding_usd': 7.12, 'liquidations': 0, 'avg_lev': 8.94, 'long': 105, 'short': 53}
- Test: {'trades': 103, 'win_rate': 41.7, 'return_pct': -7.27, 'max_dd_pct': -20.62, 'profit_factor': 0.87, 'avg_r': -0.064, 'median_r': -0.963, 'fees_usd': 51.94, 'funding_usd': -0.98, 'liquidations': 0, 'avg_lev': 8.99, 'long': 22, 'short': 81}
- Variante con funding mean reversion attiva: train +4.92%, test -7.03% (quasi nessun segnale: lasciata spenta).
- Periodo intero: {'trades': 258, 'win_rate': 41.5, 'return_pct': -2.0, 'max_dd_pct': -22.47, 'profit_factor': 0.99, 'avg_r': 0.004, 'median_r': -0.801, 'fees_usd': 135.78, 'funding_usd': 6.05, 'liquidations': 0, 'avg_lev': 8.95, 'long': 127, 'short': 131}
- Uscite: {'stop': 148, 'trailing': 105, 'max_hold': 3, 'fine_periodo': 2}

## Lettura onesta
- Nessun vantaggio dimostrato: nel test fuori campione la variante scelta perde (−7,3%), il profit factor è sotto 1 e il drawdown arriva al 20%.
- Prima dei costi la strategia è di poco positiva; le fee (taker su ~260 trade a leva ~9x) si mangiano circa il 13% del capitale in 7 settimane. Sono il problema principale.
- Nessuna liquidazione: lo stop scatta sempre molto prima del prezzo di liquidazione.
- Solo 7 settimane, un solo regime di mercato (BTC +8% poi −8%), universo scelto con i volumi di oggi (bias di sopravvivenza).
- La variante del test più alta (+8,3%, Donchian 20) era negativa nel train: sceglierla sarebbe overfitting.
- Conclusione: il modulo gira in paper per raccogliere dati e trade ombra; non va portato al live finché il paper non mostra un vantaggio netto dopo almeno 50–100 trade.
