"""Strategy versions: which rules and settings produced a trade plan.

A trade plan is the output of a fixed set of rules (the scoring thresholds in
analysis/, the stop and sizing rules, the exit-scan constants) and a handful
of settings (the confidence bar, risk per trade, the portfolio caps, the AI
overlay switches). Change either and the same market data can produce a
different decision, so any later measurement ("how did plans at 56% confidence
do?", "did last week's settings change help?") is only meaningful if each plan
says which rules it was made under.

`snapshot.py` builds the decision-relevant snapshot and its fingerprint
(pure, no database); `service.py` turns a fingerprint into a numbered
`StrategyVersion` row, created lazily the first time a plan is generated
under it.
"""
