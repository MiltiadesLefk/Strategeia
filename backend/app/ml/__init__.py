"""Forecast Lab: a gradient-boosted model trained on stored backtest trades.

The model is an OPINION, never a decision maker. Rules choose direction, entry, stop
and size; the model may only stop a trade the rules already approved, and only in a
sleeve whose style is "ml". Heavy libraries (lightgbm, shap, scikit-learn) are in
requirements-ml.txt and imported lazily, so the app runs without them.
"""
