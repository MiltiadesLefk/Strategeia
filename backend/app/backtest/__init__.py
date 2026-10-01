"""The backtester: replays the live strategy over past days.

It reuses the code that trades (generate_trade_plan for the decision, the paper
trading engine for fills and exits) and replaces only the world underneath it:
the data (local price history, cut off at the simulated moment), the clock (a
simulated one), the database (a throwaway in-memory one) and the LLM (none).
See runner.py for the day loop and data_provider.py for the as-of rules.
"""
