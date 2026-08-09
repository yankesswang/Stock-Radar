"""
Offline checks for the Pro-locked forecast fallback.

Everything network-facing is stubbed so this runs without hitting Nasdaq or
Yahoo, which also makes the rebasing arithmetic easy to assert exactly.

    uv run python scripts/test_forecast_fallback.py
"""
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.scrapers import forecast_fallback as fb


def nasdaq_rows(*specs):
    """(fiscal_end, avg, high, low, n) tuples -> parsed Nasdaq rows."""
    return [
        {
            'fiscal_end': fiscal_end,
            'end_date': fb._parse_month_year(fiscal_end),
            'avg': avg, 'high': high, 'low': low,
            'num_estimates': n, 'revisions_up': 0, 'revisions_down': 0,
        }
        for fiscal_end, avg, high, low, n in specs
    ]


def base_payload(years, eps_avg, eps_high, eps_low, endings, periods=None,
                 rev_avg=None):
    periods = periods or [f'FY {y}' for y in years]
    return {
        'ticker': 'TEST',
        'annual_forecast': {
            'periods': periods,
            'period_endings': endings,
            'eps': [None] * len(periods),
            'eps_growth': [None] * len(periods),
            'revenue': [None] * len(periods),
            'revenue_growth': [None] * len(periods),
        },
        'forecast_ranges': {
            'eps': {'years': list(years), 'avg': list(eps_avg),
                    'high': list(eps_high), 'low': list(eps_low)},
            'eps_growth': {'years': list(years), 'avg': [None] * len(years),
                           'high': [None] * len(years), 'low': [None] * len(years)},
            'revenue': {'years': list(years),
                        'avg': list(rev_avg or [None] * len(years)),
                        'high': [None] * len(years), 'low': [None] * len(years)},
            'revenue_growth': {'years': list(years), 'avg': [None] * len(years),
                               'high': [None] * len(years), 'low': [None] * len(years)},
        },
    }


def run(name, fn):
    try:
        fn()
    except AssertionError as e:
        print(f"FAIL  {name}\n      {e}")
        return False
    print(f"ok    {name}")
    return True


def approx(a, b, tol=1e-3):
    return a is not None and abs(a - b) <= tol


# ---------------------------------------------------------------------------

def test_date_parsing():
    assert fb._parse_month_year('Jan 2027') == date(2027, 1, 31)
    assert fb._parse_month_year('Feb 2028') == date(2028, 2, 29), 'leap year'
    assert fb._parse_month_year('garbage') is None
    assert fb._parse_full_date('Sep 30, 2027') == date(2027, 9, 30)
    assert fb._parse_full_date('Nov 1, 2025') == date(2025, 11, 1)
    assert fb._year_of('FY 2027') == 2027
    assert fb._year_of('2027') == 2027


def test_rebase_modes():
    # Both anchors positive -> multiplicative, preserving the source's growth.
    value, method = fb._rebase(24.41, 19.81, 22.33)
    assert method == 'ratio', method
    assert approx(value, 24.41 * 22.33 / 19.81), value
    assert approx((value / 24.41 - 1) * 100, (22.33 / 19.81 - 1) * 100), 'growth preserved'

    # Loss-making anchor -> additive, since a ratio through zero is nonsense.
    value, method = fb._rebase(-1.20, -0.80, 0.40)
    assert method == 'delta', method
    assert approx(value, -1.20 + 1.20), value

    assert fb._rebase(None, 1.0, 2.0) == (None, None)
    assert fb._rebase(1.0, None, 2.0) == (None, None)
    assert fb._rebase(1.0, 1.0, None) == (None, None)


def test_eps_fill_rebases_off_stockanalysis_level():
    """The ADBE case: Nasdaq's EPS definition sits ~19% below StockAnalysis's."""
    data = base_payload(
        years=['2026', '2027', '2028'],
        eps_avg=[24.41, None, None],
        eps_high=[26.00, None, None],
        eps_low=[22.00, None, None],
        endings=['Nov 30, 2026', 'Nov 30, 2027', 'Nov 30, 2028'],
    )
    rows = nasdaq_rows(
        ('Nov 2026', 19.81, 21.00, 18.00, 15),
        ('Nov 2027', 22.33, 24.00, 20.00, 16),
        ('Nov 2028', 24.81, 26.00, 23.00, 3),
    )
    fb.fetch_nasdaq_yearly_eps = lambda t: rows
    fb.fetch_yf_revenue_estimate = lambda t, **kw: None

    report = fb.apply_forecast_fallback(data)
    eps = data['forecast_ranges']['eps']

    assert report['applied'] is True
    assert eps['avg'][0] == 24.41, 'known year must stay untouched'
    assert approx(eps['avg'][1], 24.41 * 22.33 / 19.81), eps['avg'][1]
    assert approx(eps['avg'][2], 24.41 * 24.81 / 19.81), eps['avg'][2]
    assert eps['avg'][1] > rows[1]['avg'], 'raw Nasdaq value must not be pasted in'
    assert eps['source'] == ['stockanalysis', 'nasdaq', 'nasdaq'], eps['source']


def test_growth_uses_previous_average_as_base():
    """All three bands divide by the prior year's avg — StockAnalysis's convention."""
    data = base_payload(
        years=['2026', '2027'],
        eps_avg=[1.48, None], eps_high=[1.66, None], eps_low=[1.41, None],
        endings=['Dec 31, 2026', 'Dec 31, 2027'],
    )
    fb.fetch_nasdaq_yearly_eps = lambda t: nasdaq_rows(
        ('Dec 2026', 1.48, 1.61, 1.41, 7),
        ('Dec 2027', 1.73, 1.86, 1.65, 7),
    )
    fb.fetch_yf_revenue_estimate = lambda t, **kw: None

    fb.apply_forecast_fallback(data)
    eps = data['forecast_ranges']['eps']
    growth = data['forecast_ranges']['eps_growth']

    for band in ('avg', 'high', 'low'):
        expected = (eps[band][1] / eps['avg'][0] - 1) * 100
        assert approx(growth[band][1], round(expected, 2)), f"{band}: {growth[band][1]}"

    assert growth['high'][1] > growth['avg'][1] > growth['low'][1], (
        f"band ordering inverted: {growth['high'][1]} / "
        f"{growth['avg'][1]} / {growth['low'][1]}"
    )


def test_fiscal_end_drift_still_matches():
    """52/53-week calendars drift across the month boundary; matching must survive."""
    data = base_payload(
        years=['2026', '2027'],
        eps_avg=[12.34, None], eps_high=[13.0, None], eps_low=[11.0, None],
        endings=['Nov 1, 2026', 'Oct 31, 2027'],
    )
    fb.fetch_nasdaq_yearly_eps = lambda t: nasdaq_rows(
        ('Oct 2026', 12.42, 13.1, 11.2, 11),
        ('Oct 2027', 14.63, 15.5, 13.0, 10),
    )
    fb.fetch_yf_revenue_estimate = lambda t, **kw: None

    fb.apply_forecast_fallback(data)
    assert data['forecast_ranges']['eps']['avg'][1] is not None, 'Nov 1 vs Oct 31 must match'


def test_year_mismatch_is_refused():
    """A source whose fiscal years don't line up must fill nothing, not guess."""
    data = base_payload(
        years=['2026', '2027'],
        eps_avg=[5.0, None], eps_high=[6.0, None], eps_low=[4.0, None],
        endings=['Dec 31, 2026', 'Dec 31, 2027'],
    )
    fb.fetch_nasdaq_yearly_eps = lambda t: nasdaq_rows(
        ('Dec 2031', 9.0, 10.0, 8.0, 4),
        ('Dec 2032', 11.0, 12.0, 10.0, 4),
    )
    fb.fetch_yf_revenue_estimate = lambda t, **kw: None

    report = fb.apply_forecast_fallback(data)
    assert data['forecast_ranges']['eps']['avg'][1] is None, 'must not fill on a bad match'
    assert report['applied'] is False
    assert report['notes'], 'a refusal must be explained'


def test_thin_coverage_is_flagged():
    data = base_payload(
        years=['2026', '2027'],
        eps_avg=[3.83, None], eps_high=[4.0, None], eps_low=[3.5, None],
        endings=['Dec 31, 2026', 'Dec 31, 2027'],
    )
    fb.fetch_nasdaq_yearly_eps = lambda t: nasdaq_rows(
        ('Dec 2026', 3.21, 3.4, 3.0, 2),
        ('Dec 2027', 4.36, 4.6, 4.1, 2),
    )
    fb.fetch_yf_revenue_estimate = lambda t, **kw: None

    report = fb.apply_forecast_fallback(data)
    filled = [f for f in report['filled'] if f['field'] == 'eps']
    assert filled and filled[0]['thin_coverage'] is True, filled


def test_revenue_fill_from_yahoo():
    data = base_payload(
        years=['2026', '2027', '2028'],
        eps_avg=[1.0, None, None], eps_high=[1.1, None, None], eps_low=[0.9, None, None],
        endings=['Dec 31, 2026', 'Dec 31, 2027', 'Dec 31, 2028'],
        rev_avg=[1.10e9, None, None],
    )
    fb.fetch_nasdaq_yearly_eps = lambda t: None
    fb.fetch_yf_revenue_estimate = lambda t, **kw: {'current': 1.09e9, 'next': 1.22e9}

    report = fb.apply_forecast_fallback(data)
    rev = data['forecast_ranges']['revenue']

    assert approx(rev['avg'][1], 1.10e9 * 1.22 / 1.09, tol=1e4), rev['avg'][1]
    assert rev['avg'][2] is None, 'Yahoo only publishes +1y'
    assert report['sources'].get('revenue') == 'yfinance'
    growth = data['forecast_ranges']['revenue_growth']['avg'][1]
    assert approx(growth, round((1.22 / 1.09 - 1) * 100, 2), tol=0.05), growth


def test_fetch_failure_reason_is_not_overwritten():
    """A throttled Yahoo must not read as 'this ticker has no revenue consensus'."""
    def throttled(ticker, **kwargs):
        kwargs['notes'].append('yfinance: rate limited by Yahoo after 3 attempts — '
                               'revenue left unfilled, not absent')
        return None

    data = base_payload(
        years=['2026', '2027'],
        eps_avg=[1.0, 1.2], eps_high=[1.1, 1.3], eps_low=[0.9, 1.1],
        endings=['Dec 31, 2026', 'Dec 31, 2027'],
        rev_avg=[1e9, None],
    )
    fb.fetch_nasdaq_yearly_eps = lambda t: None
    fb.fetch_yf_revenue_estimate = throttled

    report = fb.apply_forecast_fallback(data)
    assert len(report['notes']) == 1, report['notes']
    assert 'rate limited' in report['notes'][0], report['notes']
    assert data['forecast_ranges']['revenue']['avg'][1] is None


def test_missing_period_endings_records_the_guess():
    data = base_payload(
        years=['2026', '2027'],
        eps_avg=[1.48, None], eps_high=[1.6, None], eps_low=[1.4, None],
        endings=[],  # StockAnalysis omitted the Period Ending row
        periods=[],
    )
    fb.fetch_nasdaq_yearly_eps = lambda t: nasdaq_rows(
        ('Dec 2026', 1.48, 1.6, 1.4, 7),
        ('Dec 2027', 1.73, 1.86, 1.65, 7),
    )
    fb.fetch_yf_revenue_estimate = lambda t, **kw: None

    report = fb.apply_forecast_fallback(data)
    assert data['forecast_ranges']['eps']['avg'][1] is not None, 'should still fill'
    assert any('calendar year' in n for n in report['notes']), report['notes']


def _rollover_payload():
    """
    The SNDK/WDC shape: a June fiscal year that has just ended, so Nasdaq has
    dropped FY2026 while StockAnalysis still shows its stale estimate.
    """
    data = base_payload(
        years=['2026', '2027', '2028'],
        eps_avg=[66.68, None, None],
        eps_high=[70.0, None, None],
        eps_low=[60.0, None, None],
        endings=['Jun 30, 2026', 'Jun 30, 2027', 'Jun 30, 2028'],
    )
    fb.fetch_nasdaq_yearly_eps = lambda t: nasdaq_rows(
        ('Jun 2027', 185.89, 200.0, 170.0, 8),
        ('Jun 2028', 153.20, 165.0, 140.0, 5),
    )
    fb.fetch_yf_revenue_estimate = lambda t, **kw: None
    return data


def test_rollover_leaves_uncalibrated_years_empty_by_default():
    data = _rollover_payload()
    report = fb.apply_forecast_fallback(data)

    assert data['forecast_ranges']['eps']['avg'] == [66.68, None, None], (
        'without an overlapping year the level cannot be calibrated, so nothing '
        f"should be filled: {data['forecast_ranges']['eps']['avg']}"
    )
    assert report['applied'] is False
    assert any('cannot be calibrated' in n for n in report['notes']), report['notes']


def test_rollover_takes_raw_values_when_opted_in():
    data = _rollover_payload()
    report = fb.apply_forecast_fallback(data, allow_unanchored=True)
    eps = data['forecast_ranges']['eps']

    assert eps['avg'] == [66.68, 185.89, 153.20], eps['avg']
    raw_fills = [f for f in report['filled'] if f['field'] == 'eps']
    assert {f['method'] for f in raw_fills} == {'raw'}, 'raw fills must be labelled'
    assert all(f['anchor_year'] is None for f in raw_fills), (
        'nothing was calibrated, so no anchor year may be claimed: '
        f"{[f['anchor_year'] for f in raw_fills]}"
    )
    assert any('RAW' in n for n in report['notes']), report['notes']


def test_year_with_no_source_estimate_is_reported():
    """The VIAV shape: Nasdaq covers the anchor year but not the one after."""
    data = base_payload(
        years=['2027', '2028'],
        eps_avg=[1.40, None], eps_high=[1.5, None], eps_low=[1.3, None],
        endings=['Jun 30, 2027', 'Jun 30, 2028'],
    )
    fb.fetch_nasdaq_yearly_eps = lambda t: nasdaq_rows(('Jun 2027', 1.02, 1.1, 0.95, 6))
    fb.fetch_yf_revenue_estimate = lambda t, **kw: None

    report = fb.apply_forecast_fallback(data)
    assert data['forecast_ranges']['eps']['avg'][1] is None
    assert any('no estimate for 2028' in n for n in report['notes']), (
        f'an empty year must say why, not skip silently: {report["notes"]}'
    )


def test_annual_forecast_timeline_mirrors_fills():
    data = base_payload(
        years=['2026', '2027'],
        eps_avg=[1.48, None], eps_high=[1.6, None], eps_low=[1.4, None],
        endings=['Dec 31, 2025', 'Dec 31, 2026', 'Dec 31, 2027'],
        periods=['FY 2025', 'FY 2026', 'FY 2027'],
    )
    # The long timeline carries one extra historical column.
    data['annual_forecast']['eps'] = [1.02, 1.48, None]
    data['annual_forecast']['eps_growth'] = [37.84, 45.1, None]
    fb.fetch_nasdaq_yearly_eps = lambda t: nasdaq_rows(
        ('Dec 2026', 1.48, 1.6, 1.4, 7),
        ('Dec 2027', 1.73, 1.86, 1.65, 7),
    )
    fb.fetch_yf_revenue_estimate = lambda t, **kw: None

    fb.apply_forecast_fallback(data)
    assert approx(data['annual_forecast']['eps'][2], 1.73), data['annual_forecast']['eps']
    assert data['annual_forecast']['eps'][:2] == [1.02, 1.48], 'history must not move'
    assert data['annual_forecast']['eps_growth'][2] is not None


def test_no_locked_years_skips_network():
    calls = []
    data = base_payload(
        years=['2026', '2027'],
        eps_avg=[1.0, 1.2], eps_high=[1.1, 1.3], eps_low=[0.9, 1.1],
        endings=['Dec 31, 2026', 'Dec 31, 2027'],
        rev_avg=[1e9, 2e9],
    )
    fb.fetch_nasdaq_yearly_eps = lambda t: calls.append('nasdaq')
    fb.fetch_yf_revenue_estimate = lambda t, **kw: calls.append('yf')

    report = fb.apply_forecast_fallback(data)
    assert calls == [], f"nothing was locked, but fetched: {calls}"
    assert report['applied'] is False


# ---------------------------------------------------------------------------

def main():
    real_nasdaq = fb.fetch_nasdaq_yearly_eps
    real_yf = fb.fetch_yf_revenue_estimate

    tests = [
        ('date parsing', test_date_parsing),
        ('rebase modes', test_rebase_modes),
        ('eps rebased off stockanalysis level', test_eps_fill_rebases_off_stockanalysis_level),
        ('growth uses previous average as base', test_growth_uses_previous_average_as_base),
        ('fiscal end drift still matches', test_fiscal_end_drift_still_matches),
        ('year mismatch is refused', test_year_mismatch_is_refused),
        ('thin coverage is flagged', test_thin_coverage_is_flagged),
        ('revenue fill from yahoo', test_revenue_fill_from_yahoo),
        ('fetch failure reason is not overwritten', test_fetch_failure_reason_is_not_overwritten),
        ('missing period endings records the guess', test_missing_period_endings_records_the_guess),
        ('rollover leaves uncalibrated years empty', test_rollover_leaves_uncalibrated_years_empty_by_default),
        ('rollover takes raw values when opted in', test_rollover_takes_raw_values_when_opted_in),
        ('year with no source estimate is reported', test_year_with_no_source_estimate_is_reported),
        ('annual forecast timeline mirrors fills', test_annual_forecast_timeline_mirrors_fills),
        ('no locked years skips network', test_no_locked_years_skips_network),
    ]

    passed = 0
    try:
        for name, fn in tests:
            if run(name, fn):
                passed += 1
    finally:
        fb.fetch_nasdaq_yearly_eps = real_nasdaq
        fb.fetch_yf_revenue_estimate = real_yf

    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == '__main__':
    sys.exit(main())
