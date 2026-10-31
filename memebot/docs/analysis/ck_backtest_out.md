
## Piani d'uscita sui percorsi reali registrati dal bot (52 trade shadow validi con price_path)
- attuale: n=52 win 25% medio -4.6% mediana -13.9% somma -239% maxDD -555%
- capitale a 3x (TP3 +200 vende 33%): n=52 win 25% medio -1.1% mediana -13.9% somma -59% maxDD -555%
- capitale a 5x (TP3 +400 vende 33%): n=52 win 25% medio -1.4% mediana -13.9% somma -72% maxDD -555%
- moonbag 15% senza trailing: n=52 win 25% medio -5.1% mediana -14.1% somma -265% maxDD -565%
- moonbag 15% trailing 40%: n=52 win 25% medio -5.2% mediana -13.9% somma -271% maxDD -560%
- solo capitale a 3x + moonbag (TP1 +200 vende 50%, trailing 25%): n=52 win 15% medio -11.8% mediana -15.4% somma -615% maxDD -728%

## Piani d'uscita sul backtest survivor GeckoTerminal (43 trade) e newpools (3)
- attuale: survivor n=43 win 49% medio +6.5% mediana -3.4% somma +278% maxDD -83% | newpools n=3 win 0% medio -15.0% mediana -15.0% somma -45% maxDD -45%
- capitale a 3x (TP3 +200 vende 33%): survivor n=43 win 49% medio +5.4% mediana -3.4% somma +234% maxDD -83% | newpools n=3 win 0% medio -15.0% mediana -15.0% somma -45% maxDD -45%
- capitale a 5x (TP3 +400 vende 33%): survivor n=43 win 49% medio +6.5% mediana -3.4% somma +278% maxDD -83% | newpools n=3 win 0% medio -15.0% mediana -15.0% somma -45% maxDD -45%
- moonbag 15% senza trailing: survivor n=43 win 49% medio +8.9% mediana -3.4% somma +383% maxDD -45% | newpools n=3 win 0% medio -15.0% mediana -15.0% somma -45% maxDD -45%
- moonbag 15% trailing 40%: survivor n=43 win 49% medio +9.0% mediana -3.4% somma +386% maxDD -51% | newpools n=3 win 0% medio -15.0% mediana -15.0% somma -45% maxDD -45%
- solo capitale a 3x + moonbag (TP1 +200 vende 50%, trailing 25%): survivor n=43 win 49% medio +12.6% mediana -3.4% somma +541% maxDD -69% | newpools n=3 win 0% medio -15.0% mediana -15.0% somma -45% maxDD -45%
- picco entro 180 min (survivor+bot, n=95): >= +100% 24, >= +200% (3x) 12, >= +400% (5x) 4

## (b) Conferma su candela chiusa vs ombra (survivor+newpools, regole d'uscita attuali)
- ombra: n=43 win 49% medio +6.6% mediana -3.4% somma +285% maxDD -120%
- chiusura: n=43 win 49% medio +6.1% mediana -3.4% somma +263% maxDD -83%
- chiusura_forte: n=43 win 49% medio +5.3% mediana -15.0% somma +227% maxDD -83%
- chiusura+conferma: n=41 win 46% medio +5.0% mediana -15.0% somma +206% maxDD -151%

## (c) Posizione del prezzo nel range delle ultime 30 barre all'ingresso (survivor+newpools, uscite attuali)
- basso (<0.33): n=4 win 75% medio +17.5% mediana +21.1% somma +70% maxDD -15%
- meta (0.33-0.66): n=6 win 33% medio -5.7% mediana -15.0% somma -34% maxDD -45%
- alto (>=0.66): n=36 win 44% medio +5.5% mediana -7.8% somma +197% maxDD -99%
- ritorno 5 min all'ingresso < 0: n=5 win 40% medio -2.8% mediana -15.0% somma -14% maxDD -45% | >= 0: n=41 win 46% medio +6.0% mediana -3.6% somma +247% maxDD -75%
- ultimi 15 min < -10% (lama): n=5 win 40% medio -0.5% mediana -15.0% somma -3% maxDD -30% | altri n=41 win 46% medio +5.7% mediana -3.6% somma +235% maxDD -75%

## (d) Regime di mercato all'ingresso (survivor+newpools; uscite attuali)
- SOL 4h <= -2%: n=18 win 50% medio +9.3% mediana -5.8% somma +168% maxDD -45%
- SOL 4h -2..+2%: n=23 win 39% medio +1.8% mediana -15.0% somma +42% maxDD -76%
- SOL 4h >= +2%: n=5 win 60% medio +4.5% mediana +11.9% somma +22% maxDD -18%
- BTC 4h <= -1.5%: n=15 win 40% medio +7.8% mediana -15.0% somma +116% maxDD -52%
- BTC 4h > -1.5%: n=31 win 48% medio +3.8% mediana -3.4% somma +116% maxDD -60%
- ingressi nella finestra del panico (29/10 19:00 - 30/10 13:00 Roma, BTC da ~80.8k a ~77.3k): n=26 win 38% medio -1.3% mediana -15.0% somma -35% maxDD -90%
- fuori finestra: n=20 win 55% medio +13.4% mediana +1.4% somma +267% maxDD -45%

## (d) Trade shadow del bot: token in calo a 5 min mentre SOL sale
- token giu e SOL 1h su: n=20 medio -18.0% | token giu e SOL 1h giu/piatto: n=2 medio -17.6%
- SOL 4h durante i trade del bot (30-31/10): min -0.9%, max +1.8%
