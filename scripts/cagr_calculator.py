#!/usr/bin/env python3
"""Simple CAGR (Compound Annual Growth Rate) calculator"""

final_value = 7.0
initial_value = 3.22
number_of_years = 4
cagr = (final_value / initial_value) ** (1 / number_of_years) - 1
print(f"CAGR: {cagr:.2%}")
