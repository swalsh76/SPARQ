"""The edge study: does Kalshi's price carry a calibration edge after fees?

The library behind K0.5 (KAPS PLAN.md §6.9, decisions D6 and D9 to D13). It reads the downloaded
Parquet history, builds one row per trade with its outcome, fee and time to expiry, splits the
events by time, and measures calibration and the after-fee taker and maker edge with intervals
that resample whole events.

Money stays exact: edge sums are integers (polars ``Int128``, in units of one hundredth of a
millionth of a dollar), and only the ratios, intervals and the bootstrap are floats, because they
are statistics and not money.
"""
