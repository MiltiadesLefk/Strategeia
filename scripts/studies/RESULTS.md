# Study results: do simple signals beat the market? (paper-trading research, not investment advice)

These are read-only studies of ideas for the paper-trading app. Nothing here trades anything. Each study's rules were written down before any
result was produced and were not changed afterwards; every number below is copied from the output of the script named in the section.
Run from the repo root, for example `backend/.venv/Scripts/python scripts/studies/picker_sp500_ablation_threshold.py --db runtime/picker_study.db`
(the databases are local and not part of the repo).

**How to read the tables.** "Picks" are equal-weight, bought at the next trading day's open after the signal and sold at the close after the
holding period (1, 3, 6, 12 or 24 months); no stops and, unless a line says otherwise, no trading costs. "Basket percentile" is the share of
1,000 random baskets of the same size (seed 7) that the picks beat, averaged over months: 50 is luck. Intervals are plain 95% intervals of
monthly differences and are TOO NARROW whenever holding periods overlap (3m and longer). A signal counted as working only if it beat SPY and
the average stock over 3 and 6 months, beat at least 65% of random baskets, and had an interval above zero.

## Summary (plain words)
- **Momentum and quality ranking on the S&P 500, 2023 to 2026** (a mostly rising market): momentum, especially the top tenth, beat SPY and
  most random baskets; quality did too, more weakly. The stock lists are today's index (survivors), so this is flattering.
- **The same momentum idea over 2011 to 2026** (point-in-time index membership): the edge was small and did not clear the bar; a "hold cash when
  SPY is below its 200-day average" filter did not help. The strong 2023-2026 result was largely the period.
- **Insider and Congress buying** as signals: no edge found, inside or outside the S&P 500 (insiders 2022-2026, House members 2025-2026 only).
  Earnings beats showed a weak positive; negative 8-K filings a faint negative that was not significant.
- Not tested: headline news (no history), Senate and presidential trades (no usable data), fees and taxes, and any trading rule beyond buy and hold.
- Common caveats: survivorship (current index lists, delisted tickers missing from free price data), one bull-market stretch, overlapping windows,
  no costs in the main tables.

---
## 1. Top 6 of 60 hand-picked stocks, monthly, 2023-2026 (`scripts/studies/picker_top6_60stocks.py`)
The 60 large caps were chosen in 2026 (hindsight), so treat this as the most flattering and least reliable of the studies. Parts: revenue growth,
6-1 month momentum, 30-day catalysts; equal-weight rank average; the top 6 held.
```
Month-ends scored: 2023-01-31 .. 2026-10-09 (46 month-ends)

Hold | n | Picks avg | SPY avg | All-60 avg | Picks - SPY (95% interval) | beat all-60 | random-basket percentile (avg)
1m | 44 | +3.50% | +1.50% | +1.93% | +1.99% (+0.29% to +3.70%) | 24 of 44 | 55.9th
     first month 2023-01-31, last month 2026-08-31
3m | 42 | +13.75% | +5.14% | +6.70% | +8.61% (+4.58% to +12.64%) | 27 of 42 | 65.7th
     first month 2023-01-31, last month 2026-06-30
6m | 39 | +28.26% | +10.69% | +14.08% | +17.57% (+11.92% to +23.21%) | 30 of 39 | 74.3th
     first month 2023-01-31, last month 2026-03-31
(interval is a plain 95% interval of monthly differences; overlapping 3m/6m windows make it too narrow)

SPY in period: years 2023..2026; max drawdown from a peak -18.8% on 2025-04-08; fell >10%: yes

1m:
```
## 2. S&P 500 (503 stocks): which part does the work, and a threshold rule
Scripts: `picker_sp500_ablation_threshold.py` (2a, 2b), `picker_uncapped_per_source.py` (2c), `picker_top_fifth_vs_tenth.py` (2d). Rules: buy
every stock whose rank is in the top fifth (score >= 0.80) of momentum only, quality only, catalyst only, or all three averaged; the threshold
rule needs score >= 0.80 plus positive momentum and positive revenue growth when known.
### 2a. Catalyst = earnings surprises + House trades
```
universe: 503 in sp500.csv, 502 with stored prices; SPY present: True
catalyst kinds counted: ('earnings', 'congress'); threshold 0.8; months from 2023-01-01

month-ends scored: 2023-01-31 .. 2026-10-09 (46)

Variant | Hold | n | picks avg | SPY avg | universe avg | picks-SPY (95%) | beat universe | basket pctile | avg size | cash months
momentum only | 1m | 44 | +1.89% | +1.50% | +1.31% | +0.38% (-0.47% to +1.23%) | 26 of 44 | 58.9th | 99.5 | 0
momentum only | 3m | 42 | +7.48% | +5.14% | +4.93% | +2.35% (+0.79% to +3.90%) | 32 of 42 | 71.4th | 99.5 | 0
momentum only | 6m | 39 | +18.71% | +10.69% | +10.62% | +8.02% (+4.82% to +11.22%) | 33 of 39 | 87.8th | 99.5 | 0
momentum only | 12m | 33 | +36.23% | +22.02% | +24.73% | +14.21% (+10.55% to +17.87%) | 30 of 33 | 87.0th | 99.4 | 0

quality only | 1m | 44 | +2.20% | +1.50% | +1.31% | +0.69% (-0.01% to +1.39%) | 30 of 44 | 64.0th | 84.6 | 0
quality only | 3m | 42 | +7.28% | +5.14% | +4.93% | +2.14% (+0.81% to +3.47%) | 28 of 42 | 64.8th | 84.5 | 0
quality only | 6m | 39 | +15.09% | +10.69% | +10.62% | +4.40% (+2.69% to +6.11%) | 32 of 39 | 78.3th | 84.4 | 0
quality only | 12m | 33 | +30.50% | +22.02% | +24.73% | +8.48% (+5.65% to +11.30%) | 23 of 33 | 71.6th | 84.1 | 0

catalyst only | 1m | 44 | +1.43% | +1.50% | +1.31% | -0.08% (-0.70% to +0.54%) | 24 of 44 | 53.1th | 90.0 | 0
catalyst only | 3m | 42 | +5.68% | +5.14% | +4.93% | +0.54% (-0.62% to +1.71%) | 26 of 42 | 59.4th | 88.0 | 0
catalyst only | 6m | 39 | +12.79% | +10.69% | +10.62% | +2.10% (+0.32% to +3.88%) | 29 of 39 | 66.4th | 86.3 | 0
catalyst only | 12m | 33 | +30.31% | +22.02% | +24.73% | +8.29% (+1.81% to +14.77%) | 21 of 33 | 61.7th | 84.7 | 0

all three (>=0.80) | 1m | 44 | +3.19% | +1.50% | +1.31% | +1.69% (-0.10% to +3.47%) | 26 of 44 | 60.0th | 19.1 | 0
all three (>=0.80) | 3m | 42 | +11.60% | +5.14% | +4.93% | +6.46% (+2.19% to +10.73%) | 29 of 42 | 67.9th | 18.9 | 0
all three (>=0.80) | 6m | 39 | +25.92% | +10.69% | +10.62% | +15.22% (+8.83% to +21.62%) | 34 of 39 | 81.7th | 18.8 | 0
all three (>=0.80) | 12m | 33 | +39.77% | +22.02% | +24.73% | +17.75% (+9.44% to +26.05%) | 25 of 33 | 75.9th | 18.8 | 0

THRESHOLD RULE | 1m | 44 | +3.13% | +1.50% | +1.31% | +1.63% (-0.17% to +3.42%) | 27 of 44 | 58.7th | 18.7 | 0
THRESHOLD RULE | 3m | 42 | +11.33% | +5.14% | +4.93% | +6.19% (+1.95% to +10.43%) | 30 of 42 | 68.2th | 18.5 | 0
THRESHOLD RULE | 6m | 39 | +25.52% | +10.69% | +10.62% | +14.83% (+8.54% to +21.12%) | 35 of 39 | 82.3th | 18.4 | 0
THRESHOLD RULE | 12m | 33 | +39.26% | +22.02% | +24.73% | +17.24% (+9.29% to +25.18%) | 25 of 33 | 76.5th | 18.3 | 0

(95% intervals ignore overlap between 3m/6m/12m windows, so they are too narrow; 12m has very few independent months)
SPY max drawdown from a peak in period: -18.8% on 2025-04-08
```
### 2b. The same with insider trades and 8-K filings added to the catalyst
```
universe: 503 in sp500.csv, 502 with stored prices; SPY present: True
catalyst kinds counted: ('earnings', 'congress', 'insider', 'filing_8k'); threshold 0.8; months from 2023-01-01

month-ends scored: 2023-01-31 .. 2026-10-09 (46)

Variant | Hold | n | picks avg | SPY avg | universe avg | picks-SPY (95%) | beat universe | basket pctile | avg size | cash months
momentum only | 1m | 44 | +1.89% | +1.50% | +1.31% | +0.38% (-0.47% to +1.23%) | 26 of 44 | 58.9th | 99.5 | 0
momentum only | 3m | 42 | +7.48% | +5.14% | +4.93% | +2.35% (+0.79% to +3.90%) | 32 of 42 | 71.4th | 99.5 | 0
momentum only | 6m | 39 | +18.71% | +10.69% | +10.62% | +8.02% (+4.82% to +11.22%) | 33 of 39 | 87.8th | 99.5 | 0
momentum only | 12m | 33 | +36.23% | +22.02% | +24.73% | +14.21% (+10.55% to +17.87%) | 30 of 33 | 87.0th | 99.4 | 0

quality only | 1m | 44 | +2.20% | +1.50% | +1.31% | +0.69% (-0.01% to +1.39%) | 30 of 44 | 64.0th | 84.6 | 0
quality only | 3m | 42 | +7.28% | +5.14% | +4.93% | +2.14% (+0.81% to +3.47%) | 28 of 42 | 64.8th | 84.5 | 0
quality only | 6m | 39 | +15.09% | +10.69% | +10.62% | +4.40% (+2.69% to +6.11%) | 32 of 39 | 78.3th | 84.4 | 0
quality only | 12m | 33 | +30.50% | +22.02% | +24.73% | +8.48% (+5.65% to +11.30%) | 23 of 33 | 71.6th | 84.1 | 0

catalyst only | 1m | 44 | +1.45% | +1.50% | +1.31% | -0.05% (-0.70% to +0.60%) | 29 of 44 | 56.5th | 90.1 | 0
catalyst only | 3m | 42 | +5.72% | +5.14% | +4.93% | +0.58% (-0.46% to +1.63%) | 27 of 42 | 59.5th | 88.2 | 0
catalyst only | 6m | 39 | +11.99% | +10.69% | +10.62% | +1.29% (-0.31% to +2.90%) | 25 of 39 | 61.8th | 87.0 | 0
catalyst only | 12m | 33 | +27.94% | +22.02% | +24.73% | +5.92% (+1.30% to +10.54%) | 20 of 33 | 60.6th | 85.0 | 0

all three (>=0.80) | 1m | 44 | +2.46% | +1.50% | +1.31% | +0.96% (-0.41% to +2.33%) | 25 of 44 | 56.1th | 20.0 | 0
all three (>=0.80) | 3m | 42 | +10.39% | +5.14% | +4.93% | +5.26% (+2.23% to +8.28%) | 28 of 42 | 66.4th | 19.9 | 0
all three (>=0.80) | 6m | 39 | +23.42% | +10.69% | +10.62% | +12.73% (+8.47% to +16.99%) | 34 of 39 | 83.9th | 20.1 | 0
all three (>=0.80) | 12m | 33 | +41.54% | +22.02% | +24.73% | +19.52% (+11.33% to +27.70%) | 28 of 33 | 78.3th | 19.6 | 0

THRESHOLD RULE | 1m | 44 | +2.47% | +1.50% | +1.31% | +0.97% (-0.40% to +2.34%) | 25 of 44 | 56.2th | 19.7 | 0
THRESHOLD RULE | 3m | 42 | +10.18% | +5.14% | +4.93% | +5.04% (+2.10% to +7.98%) | 29 of 42 | 66.2th | 19.5 | 0
THRESHOLD RULE | 6m | 39 | +23.21% | +10.69% | +10.62% | +12.52% (+8.42% to +16.61%) | 34 of 39 | 83.8th | 19.7 | 0
THRESHOLD RULE | 12m | 33 | +40.94% | +22.02% | +24.73% | +18.92% (+11.14% to +26.69%) | 28 of 33 | 78.4th | 19.2 | 0

(95% intervals ignore overlap between 3m/6m/12m windows, so they are too narrow; 12m has very few independent months)
SPY max drawdown from a peak in period: -18.8% on 2025-04-08
```
### 2c. Caps removed, one data source at a time, holds up to 24 months
(for the lines marked "avoid", a return below the average stock is the signal)
```
universe: 503 in sp500.csv, 502 with stored prices; cap removed (kind_cap=1000); threshold 0.8
month-ends: 2023-01-31 .. 2026-10-09 (46); stock-months with a non-zero value per kind: {'insider': 1262, 'congress': 877, 'filing_8k': 4319, 'earnings': 4582}

Variant | Hold | n months with picks (of months) | picks avg | SPY avg | universe avg | picks-universe (95%) | beat universe | basket pctile | avg size
momentum (top fifth) | 1m | 44 of 44 | +1.89% | +1.50% | +1.31% | +0.58% (-0.31% to +1.47%) | 26 of 44 | 58.9th | 99.5
momentum (top fifth) | 3m | 42 of 42 | +7.48% | +5.14% | +4.93% | +2.55% (+1.18% to +3.92%) | 32 of 42 | 71.4th | 99.5
momentum (top fifth) | 6m | 39 of 39 | +18.71% | +10.69% | +10.62% | +8.09% (+5.48% to +10.71%) | 33 of 39 | 87.8th | 99.5
momentum (top fifth) | 12m | 33 of 33 | +36.23% | +22.02% | +24.73% | +11.50% (+7.04% to +15.97%) | 30 of 33 | 87.0th | 99.4
momentum (top fifth) | 24m | 21 of 21 | +78.40% | +44.85% | +51.42% | +26.98% (+16.85% to +37.10%) | 17 of 21 | 83.8th | 99.0

quality (top fifth) | 1m | 44 of 44 | +2.20% | +1.50% | +1.31% | +0.89% (+0.14% to +1.63%) | 30 of 44 | 64.0th | 84.6
quality (top fifth) | 3m | 42 of 42 | +7.28% | +5.14% | +4.93% | +2.35% (+0.95% to +3.75%) | 28 of 42 | 64.8th | 84.5
quality (top fifth) | 6m | 39 of 39 | +15.09% | +10.69% | +10.62% | +4.47% (+2.79% to +6.15%) | 32 of 39 | 78.3th | 84.4
quality (top fifth) | 12m | 33 of 33 | +30.50% | +22.02% | +24.73% | +5.77% (+2.06% to +9.48%) | 23 of 33 | 71.6th | 84.1
quality (top fifth) | 24m | 21 of 21 | +67.84% | +44.85% | +51.42% | +16.42% (+8.17% to +24.67%) | 20 of 21 | 80.3th | 83.4

insider buyers > 0 | 1m | 44 of 44 | +1.49% | +1.50% | +1.31% | +0.18% (-0.43% to +0.79%) | 25 of 44 | 54.3th | 27.6
insider buyers > 0 | 3m | 42 of 42 | +6.14% | +5.14% | +4.93% | +1.21% (-0.02% to +2.45%) | 25 of 42 | 60.7th | 27.9
insider buyers > 0 | 6m | 39 of 39 | +11.23% | +10.69% | +10.62% | +0.61% (-1.24% to +2.46%) | 21 of 39 | 55.5th | 27.6
insider buyers > 0 | 12m | 33 of 33 | +24.78% | +22.02% | +24.73% | +0.05% (-3.73% to +3.84%) | 16 of 33 | 57.3th | 26.1
insider buyers > 0 | 24m | 21 of 21 | +47.97% | +44.85% | +51.42% | -3.45% (-9.65% to +2.74%) | 5 of 21 | 52.9th | 25.2

congress net > 0 | 1m | 20 of 44 | +3.72% | +1.33% | +1.25% | +2.47% (-0.55% to +5.48%) | 10 of 20 | 53.6th | 13.6
congress net > 0 | 3m | 18 of 42 | +5.14% | +5.32% | +5.39% | -0.26% (-5.28% to +4.77%) | 8 of 18 | 48.0th | 14.6
congress net > 0 | 6m | 15 of 39 | +11.82% | +11.80% | +12.53% | -0.71% (-6.32% to +4.90%) | 8 of 15 | 50.5th | 15.1
congress net > 0 | 12m | 9 of 33 | +36.27% | +21.64% | +32.17% | +4.10% (-23.41% to +31.62%) | 3 of 9 | 49.7th | 19.2
congress net > 0 | 24m | 0 of 21 | too few months

congress net < 0 (avoid) | 1m | 20 of 44 | +2.39% | +1.33% | +1.25% | +1.14% (-0.28% to +2.55%) | 11 of 20 | 59.5th | 25.3
congress net < 0 (avoid) | 3m | 18 of 42 | +5.57% | +5.32% | +5.39% | +0.18% (-1.99% to +2.35%) | 9 of 18 | 51.2th | 26.9
congress net < 0 (avoid) | 6m | 15 of 39 | +13.24% | +11.80% | +12.53% | +0.71% (-3.68% to +5.10%) | 7 of 15 | 54.1th | 28.7
congress net < 0 (avoid) | 12m | 9 of 33 | +44.69% | +21.64% | +32.17% | +12.52% (-12.74% to +37.79%) | 4 of 9 | 62.2th | 36.7
congress net < 0 (avoid) | 24m | 0 of 21 | too few months

8-K negative < 0 (avoid) | 1m | 44 of 44 | +1.39% | +1.50% | +1.31% | +0.08% (-0.27% to +0.43%) | 23 of 44 | 51.2th | 94.5
8-K negative < 0 (avoid) | 3m | 42 of 42 | +4.55% | +5.14% | +4.93% | -0.39% (-0.89% to +0.12%) | 16 of 42 | 43.4th | 95.5
8-K negative < 0 (avoid) | 6m | 39 of 39 | +9.97% | +10.69% | +10.62% | -0.65% (-1.52% to +0.23%) | 13 of 39 | 44.4th | 94.7
8-K negative < 0 (avoid) | 12m | 33 of 33 | +22.81% | +22.02% | +24.73% | -1.92% (-4.10% to +0.25%) | 13 of 33 | 45.5th | 93.6
8-K negative < 0 (avoid) | 24m | 21 of 21 | +54.12% | +44.85% | +51.42% | +2.69% (-3.61% to +9.00%) | 10 of 21 | 54.6th | 91.9

earnings beat > 0 | 1m | 44 of 44 | +1.36% | +1.50% | +1.31% | +0.05% (-0.46% to +0.55%) | 24 of 44 | 54.4th | 86.8
earnings beat > 0 | 3m | 42 of 42 | +6.15% | +5.14% | +4.93% | +1.22% (-0.04% to +2.47%) | 25 of 42 | 59.8th | 84.8
earnings beat > 0 | 6m | 39 of 39 | +13.78% | +10.69% | +10.62% | +3.16% (+1.20% to +5.12%) | 28 of 39 | 67.6th | 83.2
earnings beat > 0 | 12m | 33 of 33 | +32.76% | +22.02% | +24.73% | +8.03% (+1.91% to +14.15%) | 21 of 33 | 63.7th | 81.7
earnings beat > 0 | 24m | 21 of 21 | +69.91% | +44.85% | +51.42% | +18.49% (-4.51% to +41.48%) | 16 of 21 | 67.8th | 80.4

earnings miss < 0 (avoid) | 1m | 43 of 44 | +0.91% | +1.56% | +1.27% | -0.35% (-1.41% to +0.70%) | 15 of 43 | 43.8th | 17.3
earnings miss < 0 (avoid) | 3m | 41 of 42 | +4.43% | +5.14% | +4.82% | -0.39% (-2.94% to +2.16%) | 16 of 41 | 48.6th | 17.5
earnings miss < 0 (avoid) | 6m | 38 of 39 | +10.83% | +10.72% | +10.59% | +0.24% (-5.70% to +6.17%) | 15 of 38 | 48.6th | 18.1
earnings miss < 0 (avoid) | 12m | 32 of 33 | +31.90% | +22.24% | +24.91% | +6.99% (-8.37% to +22.34%) | 17 of 32 | 57.2th | 18.2
earnings miss < 0 (avoid) | 24m | 20 of 21 | +73.16% | +45.05% | +51.20% | +21.96% (-1.48% to +45.40%) | 12 of 20 | 66.2th | 19.0

ALL catalyst kinds summed (top fifth) | 1m | 44 of 44 | +1.45% | +1.50% | +1.31% | +0.14% (-0.29% to +0.57%) | 29 of 44 | 56.4th | 90.2
ALL catalyst kinds summed (top fifth) | 3m | 42 of 42 | +5.71% | +5.14% | +4.93% | +0.78% (+0.02% to +1.54%) | 27 of 42 | 59.4th | 88.3
ALL catalyst kinds summed (top fifth) | 6m | 39 of 39 | +11.98% | +10.69% | +10.62% | +1.37% (+0.25% to +2.48%) | 25 of 39 | 61.8th | 87.1
ALL catalyst kinds summed (top fifth) | 12m | 33 of 33 | +27.94% | +22.02% | +24.73% | +3.21% (-0.07% to +6.48%) | 20 of 33 | 60.5th | 85.0
ALL catalyst kinds summed (top fifth) | 24m | 21 of 21 | +55.61% | +44.85% | +51.42% | +4.19% (-2.57% to +10.96%) | 13 of 21 | 58.2th | 81.4

ALL TOGETHER: quality+momentum+catalyst >= 0.80 | 1m | 44 of 44 | +2.44% | +1.50% | +1.31% | +1.13% (-0.40% to +2.66%) | 24 of 44 | 55.4th | 20.0
ALL TOGETHER: quality+momentum+catalyst >= 0.80 | 3m | 42 of 42 | +10.44% | +5.14% | +4.93% | +5.51% (+2.28% to +8.74%) | 28 of 42 | 66.4th | 19.8
ALL TOGETHER: quality+momentum+catalyst >= 0.80 | 6m | 39 of 39 | +23.50% | +10.69% | +10.62% | +12.88% (+8.88% to +16.88%) | 34 of 39 | 83.8th | 20.0
ALL TOGETHER: quality+momentum+catalyst >= 0.80 | 12m | 33 of 33 | +41.54% | +22.02% | +24.73% | +16.81% (+8.48% to +25.14%) | 28 of 33 | 78.3th | 19.5
ALL TOGETHER: quality+momentum+catalyst >= 0.80 | 24m | 21 of 21 | +76.47% | +44.85% | +51.42% | +25.05% (+11.29% to +38.81%) | 17 of 21 | 77.0th | 18.9

THRESHOLD RULE (uncapped) | 1m | 44 of 44 | +2.45% | +1.50% | +1.31% | +1.14% (-0.40% to +2.67%) | 24 of 44 | 55.5th | 19.7
THRESHOLD RULE (uncapped) | 3m | 42 of 42 | +10.22% | +5.14% | +4.93% | +5.29% (+2.12% to +8.47%) | 29 of 42 | 66.2th | 19.5
THRESHOLD RULE (uncapped) | 6m | 39 of 39 | +23.28% | +10.69% | +10.62% | +12.67% (+8.82% to +16.51%) | 34 of 39 | 83.8th | 19.6
THRESHOLD RULE (uncapped) | 12m | 33 of 33 | +40.94% | +22.02% | +24.73% | +16.21% (+8.35% to +24.06%) | 28 of 33 | 78.3th | 19.2
THRESHOLD RULE (uncapped) | 24m | 21 of 21 | +76.11% | +44.85% | +51.42% | +24.69% (+11.35% to +38.02%) | 17 of 21 | 77.6th | 18.5

(intervals ignore overlap between windows, so they are too narrow; 12m and 24m have very few independent months)
```
### 2d. Top fifth (>= 0.80) versus top tenth (>= 0.90)
```
universe: 503 in sp500.csv, 502 with stored prices; cap removed (kind_cap=1000); bars (0.8, 0.9)
month-ends: 2023-01-31 .. 2026-10-09 (46); stock-months with a non-zero value per kind: {'insider': 1262, 'congress': 877, 'filing_8k': 4319, 'earnings': 4582}

Variant | Hold | n months with picks (of months) | picks avg | SPY avg | universe avg | picks-universe (95%) | beat universe | basket pctile | avg size
momentum >= 0.80 | 1m | 44 of 44 | +1.89% | +1.50% | +1.31% | +0.58% (-0.31% to +1.47%) | 26 of 44 | 58.9th | 99.5
momentum >= 0.80 | 3m | 42 of 42 | +7.48% | +5.14% | +4.93% | +2.55% (+1.18% to +3.92%) | 32 of 42 | 71.4th | 99.5
momentum >= 0.80 | 6m | 39 of 39 | +18.71% | +10.69% | +10.62% | +8.09% (+5.48% to +10.71%) | 33 of 39 | 87.8th | 99.5
momentum >= 0.80 | 12m | 33 of 33 | +36.23% | +22.02% | +24.73% | +11.50% (+7.04% to +15.97%) | 30 of 33 | 87.0th | 99.4
momentum >= 0.80 | 24m | 21 of 21 | +78.40% | +44.85% | +51.42% | +26.98% (+16.85% to +37.10%) | 17 of 21 | 83.8th | 99.0

momentum >= 0.90 | 1m | 44 of 44 | +2.65% | +1.50% | +1.31% | +1.35% (-0.16% to +2.85%) | 28 of 44 | 63.2th | 50.0
momentum >= 0.90 | 3m | 42 of 42 | +9.76% | +5.14% | +4.93% | +4.82% (+2.60% to +7.05%) | 33 of 42 | 74.0th | 50.0
momentum >= 0.90 | 6m | 39 of 39 | +25.19% | +10.69% | +10.62% | +14.57% (+9.76% to +19.38%) | 37 of 39 | 92.6th | 50.0
momentum >= 0.90 | 12m | 33 of 33 | +47.15% | +22.02% | +24.73% | +22.42% (+16.54% to +28.29%) | 30 of 33 | 90.7th | 50.0
momentum >= 0.90 | 24m | 21 of 21 | +109.59% | +44.85% | +51.42% | +58.17% (+40.64% to +75.69%) | 20 of 21 | 91.3th | 50.0

quality >= 0.80 | 1m | 44 of 44 | +2.20% | +1.50% | +1.31% | +0.89% (+0.14% to +1.63%) | 30 of 44 | 64.0th | 84.6
quality >= 0.80 | 3m | 42 of 42 | +7.28% | +5.14% | +4.93% | +2.35% (+0.95% to +3.75%) | 28 of 42 | 64.8th | 84.5
quality >= 0.80 | 6m | 39 of 39 | +15.09% | +10.69% | +10.62% | +4.47% (+2.79% to +6.15%) | 32 of 39 | 78.3th | 84.4
quality >= 0.80 | 12m | 33 of 33 | +30.50% | +22.02% | +24.73% | +5.77% (+2.06% to +9.48%) | 23 of 33 | 71.6th | 84.1
quality >= 0.80 | 24m | 21 of 21 | +67.84% | +44.85% | +51.42% | +16.42% (+8.17% to +24.67%) | 20 of 21 | 80.3th | 83.4

quality >= 0.90 | 1m | 44 of 44 | +2.52% | +1.50% | +1.31% | +1.21% (+0.10% to +2.32%) | 26 of 44 | 61.2th | 42.4
quality >= 0.90 | 3m | 42 of 42 | +8.58% | +5.14% | +4.93% | +3.65% (+1.56% to +5.74%) | 26 of 42 | 65.9th | 42.4
quality >= 0.90 | 6m | 39 of 39 | +17.69% | +10.69% | +10.62% | +7.07% (+4.88% to +9.26%) | 35 of 39 | 82.9th | 42.4
quality >= 0.90 | 12m | 33 of 33 | +33.84% | +22.02% | +24.73% | +9.12% (+5.75% to +12.48%) | 28 of 33 | 77.0th | 42.2
quality >= 0.90 | 24m | 21 of 21 | +63.61% | +44.85% | +51.42% | +12.19% (-2.24% to +26.62%) | 13 of 21 | 64.5th | 42.0

ALL TOGETHER (quality+momentum+catalyst) >= 0.80 | 1m | 44 of 44 | +2.44% | +1.50% | +1.31% | +1.13% (-0.40% to +2.66%) | 24 of 44 | 55.4th | 20.0
ALL TOGETHER (quality+momentum+catalyst) >= 0.80 | 3m | 42 of 42 | +10.44% | +5.14% | +4.93% | +5.51% (+2.28% to +8.74%) | 28 of 42 | 66.4th | 19.8
ALL TOGETHER (quality+momentum+catalyst) >= 0.80 | 6m | 39 of 39 | +23.50% | +10.69% | +10.62% | +12.88% (+8.88% to +16.88%) | 34 of 39 | 83.8th | 20.0
ALL TOGETHER (quality+momentum+catalyst) >= 0.80 | 12m | 33 of 33 | +41.54% | +22.02% | +24.73% | +16.81% (+8.48% to +25.14%) | 28 of 33 | 78.3th | 19.5
ALL TOGETHER (quality+momentum+catalyst) >= 0.80 | 24m | 21 of 21 | +76.47% | +44.85% | +51.42% | +25.05% (+11.29% to +38.81%) | 17 of 21 | 77.0th | 18.9

ALL TOGETHER (quality+momentum+catalyst) >= 0.90 | 1m | 44 of 44 | +4.27% | +1.50% | +1.31% | +2.96% (-0.13% to +6.04%) | 27 of 44 | 58.4th | 5.2
ALL TOGETHER (quality+momentum+catalyst) >= 0.90 | 3m | 42 of 42 | +13.55% | +5.14% | +4.93% | +8.62% (+3.58% to +13.67%) | 31 of 42 | 67.3th | 5.2
ALL TOGETHER (quality+momentum+catalyst) >= 0.90 | 6m | 39 of 39 | +29.06% | +10.69% | +10.62% | +18.44% (+10.82% to +26.06%) | 31 of 39 | 77.0th | 5.3
ALL TOGETHER (quality+momentum+catalyst) >= 0.90 | 12m | 33 of 33 | +43.19% | +22.02% | +24.73% | +18.46% (+5.18% to +31.75%) | 23 of 33 | 73.1th | 4.9
ALL TOGETHER (quality+momentum+catalyst) >= 0.90 | 24m | 21 of 21 | +89.47% | +44.85% | +51.42% | +38.05% (+12.28% to +63.82%) | 16 of 21 | 74.6th | 4.4

THRESHOLD RULE >= 0.80 | 1m | 44 of 44 | +2.45% | +1.50% | +1.31% | +1.14% (-0.40% to +2.67%) | 24 of 44 | 55.5th | 19.7
THRESHOLD RULE >= 0.80 | 3m | 42 of 42 | +10.22% | +5.14% | +4.93% | +5.29% (+2.12% to +8.47%) | 29 of 42 | 66.2th | 19.5
THRESHOLD RULE >= 0.80 | 6m | 39 of 39 | +23.28% | +10.69% | +10.62% | +12.67% (+8.82% to +16.51%) | 34 of 39 | 83.8th | 19.6
THRESHOLD RULE >= 0.80 | 12m | 33 of 33 | +40.94% | +22.02% | +24.73% | +16.21% (+8.35% to +24.06%) | 28 of 33 | 78.3th | 19.2
THRESHOLD RULE >= 0.80 | 24m | 21 of 21 | +76.11% | +44.85% | +51.42% | +24.69% (+11.35% to +38.02%) | 17 of 21 | 77.6th | 18.5

THRESHOLD RULE >= 0.90 | 1m | 44 of 44 | +4.31% | +1.50% | +1.31% | +3.01% (-0.08% to +6.09%) | 28 of 44 | 58.8th | 5.1
THRESHOLD RULE >= 0.90 | 3m | 42 of 42 | +13.45% | +5.14% | +4.93% | +8.52% (+3.50% to +13.54%) | 31 of 42 | 67.4th | 5.1
THRESHOLD RULE >= 0.90 | 6m | 39 of 39 | +29.03% | +10.69% | +10.62% | +18.41% (+10.75% to +26.08%) | 31 of 39 | 76.2th | 5.3
THRESHOLD RULE >= 0.90 | 12m | 33 of 33 | +42.41% | +22.02% | +24.73% | +17.68% (+4.48% to +30.89%) | 23 of 33 | 72.7th | 4.8
THRESHOLD RULE >= 0.90 | 24m | 21 of 21 | +89.37% | +44.85% | +51.42% | +37.95% (+10.80% to +65.09%) | 15 of 21 | 72.6th | 4.3

(intervals ignore overlap between windows, so they are too narrow; 12m and 24m have very few independent months)
```
---
## 3. Insider cluster buying, all listed stocks, 2022-2026 (`scripts/studies/insider_events_run.py`)
Event: 3 or more distinct reporting owners buying on the open market within 30 days, at least $100,000 in total, in stocks with price >= $3 and
average daily dollar volume >= $1M. Data: the SEC's quarterly Insider Transactions Data Sets (Form 4, code P). Filing date treated as end of day.
Caution: a fund family filing jointly counts as several "insiders", so many events are really one buyer; 305 of 1,653 event tickers have no price
data (delisted or renamed), which flatters the events. Verdict by the fixed bar: no evidence in any variant.
```
latest filing date in data 2026-09-30; event cutoff 2026-03-28; raw cluster events (2022-07-01..cutoff): 2806; distinct event tickers 1653
price columns loaded: 1765 of 2071 wanted; event tickers with no prices: 305 of 1653; pool tickers loaded 599 of 600 (+194 sampled tickers had no prices)
DROPPED (sequential reasons): {'no prices for ticker': 463, 'no price at D': 77, 'price < $3': 317, 'dollar volume < $1M (or <20 bars)': 647} | liquid events kept: 1302
liquid events without 200 bars of history (excluded from variant 2 only): 50 | passing gate: 277
liquid events in today's S&P 500 list: 92 | outside: 1210

Variant | Hold | n months | n events | avg return | SPY avg | pool avg | diff vs pool (95%) | basket pctile
V1 all liquid events | 1m | 45 | 1302 | +1.94% | +1.36% | +0.65% | +1.29% (+0.16% to +2.42%) | 61.6th
V1 all liquid events | 3m | 45 | 1302 | +3.75% | +4.64% | +2.50% | +1.25% (-0.59% to +3.10%) | 55.3th
V1 all liquid events | 6m | 45 | 1302 | +5.05% | +9.89% | +5.30% | -0.26% (-2.58% to +2.06%) | 52.5th
V1 all liquid events | 12m | 40 | 1079 | +11.53% | +21.11% | +11.61% | -0.08% (-5.42% to +5.26%) | 47.6th

V2 events + gate | 1m | 42 | 277 | +1.40% | +0.85% | -0.01% | +1.41% (-1.04% to +3.87%) | 55.5th
V2 events + gate | 3m | 42 | 277 | +3.94% | +4.68% | +2.57% | +1.37% (-3.06% to +5.80%) | 50.7th
V2 events + gate | 6m | 42 | 277 | +2.34% | +9.86% | +5.43% | -3.09% (-7.09% to +0.91%) | 44.6th
V2 events + gate | 12m | 37 | 210 | +11.29% | +21.01% | +12.01% | -0.73% (-8.45% to +7.00%) | 50.0th

V3 gate-only control | 1m | 45 | 1302 | +0.50% | +1.36% | +0.65% | -0.16% (-0.66% to +0.35%) | 52.6th
V3 gate-only control | 3m | 45 | 1302 | +2.63% | +4.64% | +2.50% | +0.14% (-0.75% to +1.02%) | 55.0th
V3 gate-only control | 6m | 45 | 1302 | +6.41% | +9.89% | +5.30% | +1.11% (+0.20% to +2.02%) | 58.9th
V3 gate-only control | 12m | 40 | 1079 | +12.83% | +21.11% | +11.61% | +1.22% (-0.65% to +3.09%) | 58.6th

Cost line (information): average return minus 0.3% round trip
V1 all liquid events 1m: +1.64% | 3m: +3.45% | 6m: +4.75% | 12m: +11.23%
V2 events + gate 1m: +1.10% | 3m: +3.64% | 6m: +2.04% | 12m: +10.99%

Info only: V2 minus V3 (insider cluster + gate vs gate alone, same days)
1m: n months 42, V2 minus gate-pool +1.44% (-1.05% to +3.93%)
3m: n months 42, V2 minus gate-pool +1.02% (-3.74% to +5.78%)
6m: n months 42, V2 minus gate-pool -4.25% (-8.53% to +0.03%)
12m: n months 37, V2 minus gate-pool -1.78% (-10.02% to +6.46%)

Info only: variant 1 split by today's S&P 500 membership
in S&P 500 list | 1m | 41 | 92 | +2.46% | +2.02% | +1.45% | +1.01% (-0.88% to +2.89%) | 54.2th
in S&P 500 list | 3m | 41 | 92 | +6.03% | +4.64% | +2.28% | +3.75% (-0.03% to +7.53%) | 59.0th
in S&P 500 list | 6m | 41 | 92 | +11.45% | +10.42% | +5.64% | +5.81% (+0.51% to +11.11%) | 60.2th
in S&P 500 list | 12m | 36 | 72 | +26.30% | +21.40% | +11.49% | +14.81% (+5.08% to +24.54%) | 67.5th
outside S&P 500 list | 1m | 45 | 1210 | +1.93% | +1.33% | +0.62% | +1.31% (+0.08% to +2.54%) | 60.7th
outside S&P 500 list | 3m | 45 | 1210 | +3.61% | +4.64% | +2.49% | +1.12% (-0.76% to +3.01%) | 54.5th
outside S&P 500 list | 6m | 45 | 1210 | +4.79% | +9.90% | +5.32% | -0.53% (-3.09% to +2.03%) | 51.4th
outside S&P 500 list | 12m | 40 | 1007 | +10.48% | +21.11% | +11.61% | -1.13% (-6.76% to +4.50%) | 45.0th

SUCCESS BAR (3m and 6m: beats SPY and pool, basket pctile >= 65, interval above zero, >= 24 months):
  V1 all liquid events 63: False
  V1 all liquid events 126: False
  => V1 all liquid events: no evidence
  V2 events + gate 63: False
  V2 events + gate 126: False
  => V2 events + gate: no evidence
  V3 gate-only control 63: False
  V3 gate-only control 126: False
  => V3 gate-only control: no evidence
```
---
## 4. Momentum over 2011-2026 with point-in-time S&P 500 membership and a market filter (`scripts/studies/long_history_run.py`)
Variants: V1 momentum top tenth; V2 top fifth; V3 = V1 plus revenue growth > 0; V4 = V1 only when SPY is above its 200-day average (else cash);
V5 = V3 with that filter. Index membership is rebuilt from the published list of additions and removals; 185 of 828 ever-members (184 removed names plus WBD) have no Yahoo
prices and are dropped (a survivorship gap: only 348 of 506 members were tradeable in 2011). Verdict by the fixed bar: no variant survives; the
market filter did not protect in the bad years.
```
tickers ever members since 2010-01-01: 828; with stored prices: 643; WITHOUT prices: 185 (of which removed tickers: 184, current-list tickers: 1)
no-price current-list tickers: ['WBD']
SPY bars 2007-10-24 .. 2026-10-09 (4770)
month-ends: 2011-01-31 .. 2026-09-30 (189)

COVERAGE per year (averages over that year's month-ends): members in the reconstructed index | tradeable (prices, >=127 bars) | with a revenue growth figure
2011: members 506 | tradeable 348 | with revenue figure 216 (62% of tradeable)
2012: members 506 | tradeable 360 | with revenue figure 233 (65% of tradeable)
2013: members 505 | tradeable 372 | with revenue figure 245 (66% of tradeable)
2014: members 504 | tradeable 380 | with revenue figure 256 (67% of tradeable)
2015: members 506 | tradeable 390 | with revenue figure 260 (67% of tradeable)
2016: members 508 | tradeable 409 | with revenue figure 277 (68% of tradeable)
2017: members 507 | tradeable 424 | with revenue figure 290 (68% of tradeable)
2018: members 506 | tradeable 434 | with revenue figure 303 (70% of tradeable)
2019: members 506 | tradeable 444 | with revenue figure 343 (77% of tradeable)
2020: members 506 | tradeable 456 | with revenue figure 351 (77% of tradeable)
2021: members 506 | tradeable 464 | with revenue figure 354 (76% of tradeable)
2022: members 505 | tradeable 473 | with revenue figure 366 (77% of tradeable)
2023: members 504 | tradeable 481 | with revenue figure 378 (79% of tradeable)
2024: members 504 | tradeable 485 | with revenue figure 388 (80% of tradeable)
2025: members 504 | tradeable 492 | with revenue figure 407 (83% of tradeable)
2026: members 503 | tradeable 498 | with revenue figure 422 (85% of tradeable)
V3: average basket 36.4 stocks, of which with NO revenue figure (kept): 10.7
stock-months with a return, hold 1m: 80933; exited at the last close before the exit date (stopped trading): 0
stock-months with a return, hold 3m: 79937; exited at the last close before the exit date (stopped trading): 0
stock-months with a return, hold 6m: 78443; exited at the last close before the exit date (stopped trading): 0
stock-months with a return, hold 12m: 75470; exited at the last close before the exit date (stopped trading): 0
months where SPY close <= its 200-day average (market filter says cash): 29 of 189

Variant | Hold | n months | picks avg | SPY avg | universe avg | picks-universe (95%) | beat SPY avg / universe avg | basket pctile (invested months) | avg size | cash months
V1 mom>=0.90 | 1m | 188 | +1.32% | +1.17% | +1.08% | +0.24% (-0.20% to +0.67%) | yes / yes | 54.8th | 43.5 | 0
V1 mom>=0.90 | 3m | 186 | +4.00% | +3.55% | +3.32% | +0.68% (+0.00% to +1.37%) | yes / yes | 56.2th | 43.4 | 0
V1 mom>=0.90 | 6m | 183 | +8.45% | +7.16% | +6.62% | +1.82% (+0.65% to +2.99%) | yes / yes | 61.7th | 43.3 | 0
V1 mom>=0.90 | 6m WITH 0.3% round-trip cost (info) | 183 | +8.15% | SPY +7.16% | universe +6.62% | picks-universe +1.52%
V1 mom>=0.90 | 12m | 177 | +15.79% | +15.00% | +14.15% | +1.64% (+0.08% to +3.19%) | yes / yes | 56.6th | 43.1 | 0

V2 mom>=0.80 | 1m | 188 | +1.14% | +1.17% | +1.08% | +0.06% (-0.25% to +0.36%) | no / yes | 52.7th | 86.5 | 0
V2 mom>=0.80 | 3m | 186 | +3.50% | +3.55% | +3.32% | +0.19% (-0.28% to +0.65%) | no / yes | 53.3th | 86.4 | 0
V2 mom>=0.80 | 6m | 183 | +7.55% | +7.16% | +6.62% | +0.93% (+0.16% to +1.70%) | yes / yes | 59.2th | 86.1 | 0
V2 mom>=0.80 | 6m WITH 0.3% round-trip cost (info) | 183 | +7.25% | SPY +7.16% | universe +6.62% | picks-universe +0.63%
V2 mom>=0.80 | 12m | 177 | +14.92% | +15.00% | +14.15% | +0.77% (-0.38% to +1.92%) | no / yes | 56.0th | 85.7 | 0

V3 V1+rev>0 | 1m | 188 | +1.24% | +1.17% | +1.08% | +0.16% (-0.30% to +0.62%) | yes / yes | 53.1th | 36.3 | 0
V3 V1+rev>0 | 3m | 186 | +3.72% | +3.55% | +3.32% | +0.40% (-0.33% to +1.13%) | yes / yes | 53.0th | 36.2 | 0
V3 V1+rev>0 | 6m | 183 | +8.05% | +7.16% | +6.62% | +1.43% (+0.22% to +2.65%) | yes / yes | 59.0th | 36.2 | 0
V3 V1+rev>0 | 6m WITH 0.3% round-trip cost (info) | 183 | +7.75% | SPY +7.16% | universe +6.62% | picks-universe +1.13%
V3 V1+rev>0 | 12m | 177 | +14.72% | +15.00% | +14.15% | +0.57% (-1.07% to +2.20%) | no / yes | 51.5th | 36.0 | 0

V4 V1+SPY>200d | 1m | 188 | +0.79% | +1.17% | +1.08% | -0.30% (-0.91% to +0.32%) | no / no | 54.4th | 43.4 | 29
V4 V1+SPY>200d | 3m | 186 | +2.88% | +3.55% | +3.32% | -0.43% (-1.28% to +0.41%) | no / no | 58.4th | 43.3 | 29
V4 V1+SPY>200d | 6m | 183 | +6.71% | +7.16% | +6.62% | +0.09% (-1.31% to +1.49%) | no / yes | 64.2th | 43.2 | 29
V4 V1+SPY>200d | 6m WITH 0.3% round-trip cost (info) | 183 | +6.46% | SPY +7.16% | universe +6.62% | picks-universe -0.16%
V4 V1+SPY>200d | 12m | 177 | +13.14% | +15.00% | +14.15% | -1.01% (-3.14% to +1.12%) | no / no | 60.8th | 42.9 | 28

V5 V3+SPY>200d | 1m | 188 | +0.71% | +1.17% | +1.08% | -0.38% (-1.01% to +0.25%) | no / no | 52.6th | 35.7 | 29
V5 V3+SPY>200d | 3m | 186 | +2.57% | +3.55% | +3.32% | -0.75% (-1.62% to +0.12%) | no / no | 54.6th | 35.6 | 29
V5 V3+SPY>200d | 6m | 183 | +6.32% | +7.16% | +6.62% | -0.30% (-1.73% to +1.13%) | no / no | 61.1th | 35.5 | 29
V5 V3+SPY>200d | 6m WITH 0.3% round-trip cost (info) | 183 | +6.07% | SPY +7.16% | universe +6.62% | picks-universe -0.56%
V5 V3+SPY>200d | 12m | 177 | +12.18% | +15.00% | +14.15% | -1.97% (-4.13% to +0.18%) | no / no | 55.0th | 35.3 | 28

(95% intervals ignore the overlap between windows, so they are too narrow, most of all for 6m and 12m)

PER YEAR: SPY return (prior year-end close to this year-end close; last year to the latest bar) | SPY max drawdown inside the year | avg 6m-hold return of months STARTING that year: SPY, universe, V1, V3, V4 (n months)
2011: SPY +1.9% | max DD -18.6% | 6m starts 12: SPY +2.4%, universe +1.8%, V1 -1.9%, V3 -1.5%, V4 -5.9%
2012: SPY +16.0% | max DD -9.7% | 6m starts 12: SPY +9.0%, universe +11.6%, V1 +13.6%, V3 +12.0%, V4 +13.6%
2013: SPY +32.3% | max DD -5.6% | 6m starts 12: SPY +10.8%, universe +12.6%, V1 +18.6%, V3 +18.2%, V4 +18.6%
2014: SPY +13.5% | max DD -7.3% | 6m starts 12: SPY +6.1%, universe +6.4%, V1 +5.6%, V3 +6.2%, V4 +5.6%
2015: SPY +1.2% | max DD -11.9% | 6m starts 12: SPY +0.7%, universe -0.0%, V1 -0.5%, V3 -0.2%, V4 -1.2%
2016: SPY +12.0% | max DD -9.2% | 6m starts 12: SPY +9.4%, universe +10.1%, V1 +9.8%, V3 +9.6%, V4 +8.0%
2017: SPY +21.7% | max DD -2.6% | 6m starts 12: SPY +7.5%, universe +6.9%, V1 +9.6%, V3 +8.5%, V4 +9.6%
2018: SPY -4.6% | max DD -19.3% | 6m starts 12: SPY +3.5%, universe +3.1%, V1 +1.4%, V3 +1.0%, V4 -0.7%
2019: SPY +31.2% | max DD -6.6% | 6m starts 12: SPY +3.4%, universe -0.7%, V1 +3.8%, V3 +3.8%, V4 +1.2%
2020: SPY +18.3% | max DD -33.7% | 6m starts 12: SPY +18.0%, universe +21.4%, V1 +19.3%, V3 +18.5%, V4 +14.1%
2021: SPY +28.7% | max DD -5.1% | 6m starts 12: SPY +3.4%, universe +3.7%, V1 -0.7%, V3 -4.1%, V4 -0.7%
2022: SPY -18.2% | max DD -24.5% | 6m starts 12: SPY +0.2%, universe +0.4%, V1 +1.9%, V3 +1.4%, V4 -1.7%
2023: SPY +26.2% | max DD -10.0% | 6m starts 12: SPY +12.4%, universe +7.1%, V1 +11.6%, V3 +11.9%, V4 +9.4%
2024: SPY +24.9% | max DD -8.4% | 6m starts 12: SPY +7.6%, universe +5.3%, V1 +8.3%, V3 +8.9%, V4 +8.3%
2025: SPY +17.7% | max DD -18.8% | 6m starts 12: SPY +11.3%, universe +9.1%, V1 +22.8%, V3 +23.0%, V4 +20.6%
2026: SPY +15.1% | max DD -8.9% | 6m starts 3: SPY +13.9%, universe +9.2%, V1 +22.3%, V3 +22.2%, V4 +15.1%
years where SPY fell more than 10% from its peak inside the year: [np.int64(2011), np.int64(2015), np.int64(2018), np.int64(2020), np.int64(2022), np.int64(2025)]

WORST 10 MONTHS of V1 by 6m return: month | stocks held | V1 | SPY | universe
2021-12-31 | 47 | -24.9% | -19.2% | -15.9%
2011-03-31 | 35 | -23.6% | -12.1% | -14.3%
2011-02-28 | 35 | -23.3% | -10.9% | -11.5%
2021-11-30 | 47 | -21.3% | -10.7% | -3.7%
2021-10-29 | 47 | -19.7% | -9.4% | -5.1%
2011-05-31 | 35 | -18.8% | -10.1% | -11.9%
2015-07-31 | 40 | -15.2% | -7.0% | -8.8%
2022-03-31 | 48 | -14.2% | -20.5% | -18.9%
2019-09-30 | 45 | -13.9% | -12.5% | -21.0%
2011-04-29 | 35 | -12.5% | -5.2% | -6.8%
```
```
SUCCESS BAR per variant (6m AND 12m: beats SPY avg, beats universe avg, basket pctile >= 65, interval lower end > 0; and bad-year check on 6m)
V1 mom>=0.90 6m: beats SPY True, beats universe True, pctile 61.7 (>=65 False), interval lower end +0.65% (>0 True) -> False
V1 mom>=0.90 12m: beats SPY True, beats universe True, pctile 56.6 (>=65 False), interval lower end +0.08% (>0 True) -> False
V1 mom>=0.90 bad year 2011: variant 6m avg -1.9% vs universe +1.8% -> LOSES MORE
V1 mom>=0.90 bad year 2015: variant 6m avg -0.5% vs universe -0.0% -> LOSES MORE
V1 mom>=0.90 bad year 2018: variant 6m avg +1.4% vs universe +3.1% -> LOSES MORE
V1 mom>=0.90 bad year 2020: variant 6m avg +19.3% vs universe +21.4% -> LOSES MORE
V1 mom>=0.90 bad year 2022: variant 6m avg +1.9% vs universe +0.4% -> ok
V1 mom>=0.90 bad year 2025: variant 6m avg +22.8% vs universe +9.1% -> ok
==> V1 mom>=0.90: no evidence

V2 mom>=0.80 6m: beats SPY True, beats universe True, pctile 59.2 (>=65 False), interval lower end +0.16% (>0 True) -> False
V2 mom>=0.80 12m: beats SPY False, beats universe True, pctile 56.0 (>=65 False), interval lower end -0.38% (>0 False) -> False
V2 mom>=0.80 bad year 2011: variant 6m avg -1.4% vs universe +1.8% -> LOSES MORE
V2 mom>=0.80 bad year 2015: variant 6m avg +0.7% vs universe -0.0% -> ok
V2 mom>=0.80 bad year 2018: variant 6m avg +2.6% vs universe +3.1% -> LOSES MORE
V2 mom>=0.80 bad year 2020: variant 6m avg +19.6% vs universe +21.4% -> LOSES MORE
V2 mom>=0.80 bad year 2022: variant 6m avg +0.2% vs universe +0.4% -> LOSES MORE
V2 mom>=0.80 bad year 2025: variant 6m avg +16.3% vs universe +9.1% -> ok
==> V2 mom>=0.80: no evidence

V3 V1+rev>0 6m: beats SPY True, beats universe True, pctile 59.0 (>=65 False), interval lower end +0.22% (>0 True) -> False
V3 V1+rev>0 12m: beats SPY False, beats universe True, pctile 51.5 (>=65 False), interval lower end -1.07% (>0 False) -> False
V3 V1+rev>0 bad year 2011: variant 6m avg -1.5% vs universe +1.8% -> LOSES MORE
V3 V1+rev>0 bad year 2015: variant 6m avg -0.2% vs universe -0.0% -> LOSES MORE
V3 V1+rev>0 bad year 2018: variant 6m avg +1.0% vs universe +3.1% -> LOSES MORE
V3 V1+rev>0 bad year 2020: variant 6m avg +18.5% vs universe +21.4% -> LOSES MORE
V3 V1+rev>0 bad year 2022: variant 6m avg +1.4% vs universe +0.4% -> ok
V3 V1+rev>0 bad year 2025: variant 6m avg +23.0% vs universe +9.1% -> ok
==> V3 V1+rev>0: no evidence

V4 V1+SPY>200d 6m: beats SPY False, beats universe True, pctile 64.2 (>=65 False), interval lower end -1.31% (>0 False) -> False
V4 V1+SPY>200d 12m: beats SPY False, beats universe False, pctile 60.8 (>=65 False), interval lower end -3.14% (>0 False) -> False
V4 V1+SPY>200d bad year 2011: variant 6m avg -5.9% vs universe +1.8% -> LOSES MORE
V4 V1+SPY>200d bad year 2015: variant 6m avg -1.2% vs universe -0.0% -> LOSES MORE
V4 V1+SPY>200d bad year 2018: variant 6m avg -0.7% vs universe +3.1% -> LOSES MORE
V4 V1+SPY>200d bad year 2020: variant 6m avg +14.1% vs universe +21.4% -> LOSES MORE
V4 V1+SPY>200d bad year 2022: variant 6m avg -1.7% vs universe +0.4% -> LOSES MORE
V4 V1+SPY>200d bad year 2025: variant 6m avg +20.6% vs universe +9.1% -> ok
==> V4 V1+SPY>200d: no evidence

V5 V3+SPY>200d 6m: beats SPY False, beats universe False, pctile 61.1 (>=65 False), interval lower end -1.73% (>0 False) -> False
V5 V3+SPY>200d 12m: beats SPY False, beats universe False, pctile 55.0 (>=65 False), interval lower end -4.13% (>0 False) -> False
V5 V3+SPY>200d bad year 2011: variant 6m avg -5.7% vs universe +1.8% -> LOSES MORE
V5 V3+SPY>200d bad year 2015: variant 6m avg -1.2% vs universe -0.0% -> LOSES MORE
V5 V3+SPY>200d bad year 2018: variant 6m avg -1.1% vs universe +3.1% -> LOSES MORE
V5 V3+SPY>200d bad year 2020: variant 6m avg +13.7% vs universe +21.4% -> LOSES MORE
V5 V3+SPY>200d bad year 2022: variant 6m avg -2.0% vs universe +0.4% -> LOSES MORE
V5 V3+SPY>200d bad year 2025: variant 6m avg +20.6% vs universe +9.1% -> ok
==> V5 V3+SPY>200d: no evidence
```

## Officer-only insider buying and the look-back window (`insider_officer_run.py`, 2022-07 to 2026-03 events)
Rules were fixed before the run. Data: SEC Insider Transactions Data Sets (open-market buys, Form 4), prices from the local history store.
Four variants: purchases by officers or by any insider, summed over 90 or 180 days, event when the sum reaches $100,000; one event per
ticker per 180 days. Compared with SPY, the average liquid stock and 1,000 random baskets, as above.
- **No variant counts as working.** Officer 90d at 3m: +2.73% over the average stock (interval +0.89% to +4.58%, basket percentile 64.4, just
  under the 65 bar); at 6m +0.78% with the interval across zero. All four trail SPY at 6m.
- Officers did not beat all insiders over the same window (3m about +1 point, 6m about -0.3 to -0.6 points), and 180 days was no better than 90.
- Inside today's S&P 500 list every variant looks better (all insiders, 90d, 6m: +5.86% over the average stock, interval +3.77% to +7.96%); that
  list is today's members, so it favours winners. About half of the event tickers had no prices (delisted or renamed), so survivors are
  overrepresented. Purchases are not netted against sales here, unlike the live rule.
