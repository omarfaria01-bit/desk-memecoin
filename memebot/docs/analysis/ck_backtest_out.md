
## Piani d'uscita sui percorsi reali registrati dal bot (52 trade shadow validi con price_path)
- attuale: n=52 win 21% medio -5.1% mediana -13.7% somma -265% maxDD -554%
- capitale a 3x (TP3 +200 vende 33%): n=52 win 21% medio -1.6% mediana -13.7% somma -84% maxDD -554%
- capitale a 5x (TP3 +400 vende 33%): n=52 win 21% medio -1.9% mediana -13.7% somma -98% maxDD -554%
- moonbag 15% senza trailing: n=52 win 21% medio -6.2% mediana -14.1% somma -321% maxDD -563%
- moonbag 15% trailing 40%: n=52 win 21% medio -5.7% mediana -13.7% somma -295% maxDD -558%
- solo capitale a 3x + moonbag (TP1 +200 vende 50%, trailing 25%): n=52 win 13% medio -11.8% mediana -15.4% somma -613% maxDD -726%

## Piani d'uscita sul backtest survivor GeckoTerminal (43 trade) e newpools (3)
- attuale: survivor n=43 win 33% medio +3.7% mediana -3.8% somma +158% maxDD -100% | newpools n=3 win 0% medio -14.7% mediana -15.0% somma -44% maxDD -44%
- capitale a 3x (TP3 +200 vende 33%): survivor n=43 win 33% medio +2.6% mediana -3.8% somma +114% maxDD -100% | newpools n=3 win 0% medio -14.7% mediana -15.0% somma -44% maxDD -44%
- capitale a 5x (TP3 +400 vende 33%): survivor n=43 win 33% medio +3.7% mediana -3.8% somma +158% maxDD -100% | newpools n=3 win 0% medio -14.7% mediana -15.0% somma -44% maxDD -44%
- moonbag 15% senza trailing: survivor n=43 win 33% medio +5.5% mediana -3.8% somma +238% maxDD -67% | newpools n=3 win 0% medio -14.7% mediana -15.0% somma -44% maxDD -44%
- moonbag 15% trailing 40%: survivor n=43 win 33% medio +5.0% mediana -3.8% somma +215% maxDD -84% | newpools n=3 win 0% medio -14.7% mediana -15.0% somma -44% maxDD -44%
- solo capitale a 3x + moonbag (TP1 +200 vende 50%, trailing 25%): survivor n=43 win 33% medio +9.7% mediana -3.8% somma +416% maxDD -69% | newpools n=3 win 0% medio -14.7% mediana -15.0% somma -44% maxDD -44%
- picco entro 180 min (survivor+bot, n=95): >= +100% 24, >= +200% (3x) 12, >= +400% (5x) 4

## (b) Conferma su candela chiusa vs ombra (survivor+newpools, regole d'uscita attuali)
- ombra: n=43 win 33% medio +4.0% mediana -2.7% somma +173% maxDD -152%
- chiusura: n=43 win 33% medio +3.4% mediana -4.3% somma +144% maxDD -100%
- chiusura_forte: n=43 win 30% medio +2.5% mediana -13.2% somma +106% maxDD -100%
- chiusura+conferma: n=41 win 29% medio +2.0% mediana -11.3% somma +83% maxDD -157%

## (c) Posizione del prezzo nel range delle ultime 30 barre all'ingresso (survivor+newpools, uscite attuali)
- basso (<0.33): n=4 win 75% medio +15.7% mediana +17.6% somma +63% maxDD -15%
- meta (0.33-0.66): n=6 win 17% medio -6.9% mediana -15.0% somma -41% maxDD -45%
- alto (>=0.66): n=36 win 28% medio +2.6% mediana -8.1% somma +92% maxDD -98%
- ultimi 15 min < -10% (lama): n=5 win 40% medio -2.0% mediana -15.0% somma -10% maxDD -37% | altri n=41 win 29% medio +3.0% mediana -4.3% somma +123% maxDD -72%

## (d) Regime di mercato all'ingresso (survivor+newpools; uscite attuali)
- SOL 4h <= -2%: n=18 win 33% medio +7.9% mediana -7.9% somma +143% maxDD -45%
- SOL 4h -2..+2%: n=23 win 30% medio -0.7% mediana -15.0% somma -17% maxDD -85%
- SOL 4h >= +2%: n=5 win 20% medio -2.5% mediana -2.7% somma -12% maxDD -20%
- BTC 4h <= -1.5%: n=15 win 20% medio +3.0% mediana -15.0% somma +44% maxDD -74%
- BTC 4h > -1.5%: n=31 win 35% medio +2.2% mediana -3.6% somma +69% maxDD -57%
- ingressi nella finestra del panico (29/10 19:00 - 30/10 13:00 Roma, BTC da ~80.8k a ~77.3k): n=26 win 19% medio -5.9% mediana -14.1% somma -154% maxDD -154%
- fuori finestra: n=20 win 45% medio +13.4% mediana -2.3% somma +268% maxDD -44%

## (d) Trade shadow del bot: token in calo a 5 min mentre SOL sale
- token giu e SOL 1h su: n=20 medio -18.0% | token giu e SOL 1h giu/piatto: n=2 medio -17.6%
- SOL 4h durante i trade del bot (30-31/10): min -0.9%, max +1.8%
