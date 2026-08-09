"""
Fill StockAnalysis Pro-locked forecast years from free sources.

StockAnalysis leaves the fiscal-year *labels* visible but blanks the values for
FY+1 and beyond unless you have Pro, so a scraped range table comes back as::

    {'years': ['2026', '2027', '2028'], 'avg': [1.47, None, None]}

Two keyless sources fill those holes:

* **EPS**     — Nasdaq's analyst earnings-forecast API, consensus out to ~FY+3
* **Revenue** — yfinance ``revenue_estimate``, FY+1 only

Values are *rebased* onto the StockAnalysis anchor rather than pasted in raw.
The two sources use different EPS definitions (ADBE FY2026: StockAnalysis
24.43 non-GAAP vs Nasdaq 19.81), and ``load_data.compute_valuation`` multiplies
``next_year_eps`` by a PE median derived from StockAnalysis's own historical
EPS. Pasting a foreign EPS level into that product would skew the fair price by
the definition gap; carrying over only the source's *growth* keeps the series
internally consistent. See ``_rebase``.
"""
import random
import re
import time
from calendar import monthrange
from datetime import date

from src.utils import fetch_url

NASDAQ_URL = "https://api.nasdaq.com/api/analyst/{ticker}/earnings-forecast"

NASDAQ_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/plain, */*',
}

_MONTHS = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

# 52/53-week fiscal calendars drift a few days across the month boundary
# (ADI's FY ends "Nov 1, 2025" one year and "Oct 31, 2026" the next), so match
# fiscal ends by proximity rather than by exact month.
_MATCH_TOLERANCE_DAYS = 45

# Below this many contributing analysts the consensus is one or two desks
# guessing; still filled, but flagged so it can be discounted downstream.
_THIN_COVERAGE = 3


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _parse_month_year(label):
    """'Jan 2027' -> date(2027, 1, 31). Returns None if unparseable."""
    if not label:
        return None
    m = re.match(r'\s*([A-Za-z]{3})[a-z]*\.?\s+(\d{4})\s*$', label)
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower())
    if not month:
        return None
    year = int(m.group(2))
    return date(year, month, monthrange(year, month)[1])


def _parse_full_date(label):
    """'Dec 31, 2027' -> date(2027, 12, 31). Returns None if unparseable."""
    if not label:
        return None
    m = re.match(r'\s*([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\s*$', label)
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower())
    if not month:
        return None
    return date(int(m.group(3)), month, int(m.group(2)))


def _year_of(label):
    """Pull the 4-digit year out of 'FY 2027' / '2027'. Returns None if absent."""
    m = re.search(r'(\d{4})', label or '')
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Source fetchers
# ---------------------------------------------------------------------------

def fetch_nasdaq_yearly_eps(ticker):
    """
    Fetch Nasdaq's annual consensus EPS forecast.

    Returns a list of dicts ordered nearest-first, each with ``fiscal_end``
    (the raw 'Jan 2027' label), ``end_date``, ``avg`` / ``high`` / ``low`` EPS
    and ``num_estimates``. Returns None when the request or payload fails.
    """
    response = fetch_url(
        NASDAQ_URL.format(ticker=ticker.upper()),
        NASDAQ_HEADERS,
        max_retries=3,
        timeout=15,
    )
    if response is None:
        return None

    try:
        payload = response.json()
    except ValueError:
        return None

    data = payload.get('data') or {}
    yearly = data.get('yearlyForecast') or {}
    rows = yearly.get('rows') or []

    parsed = []
    for row in rows:
        end_date = _parse_month_year(row.get('fiscalEnd'))
        if end_date is None:
            continue
        parsed.append({
            'fiscal_end': row.get('fiscalEnd'),
            'end_date': end_date,
            'avg': _as_float(row.get('consensusEPSForecast')),
            'high': _as_float(row.get('highEPSForecast')),
            'low': _as_float(row.get('lowEPSForecast')),
            'num_estimates': row.get('noOfEstimates'),
            'revisions_up': row.get('up'),
            'revisions_down': row.get('down'),
        })

    parsed.sort(key=lambda r: r['end_date'])
    return parsed or None


def fetch_yf_revenue_estimate(ticker, max_retries=3, notes=None):
    """
    Fetch Yahoo's current/next fiscal-year revenue consensus via yfinance.

    Yahoo only publishes ``0y`` and ``+1y``, so this can fill exactly one
    locked year. Returns ``{'current': float, 'next': float}`` or None.

    Yahoo throttles hard — a full-watchlist run will lose some tickers to 429s
    even with the back-off below. The reason is written to ``notes`` so a
    throttled run is never mistaken for a ticker that genuinely has no revenue
    consensus.
    """
    def note(message):
        if notes is not None:
            notes.append(f'yfinance: {message}')

    try:
        import yfinance as yf
    except ImportError:
        note('not installed')
        return None

    df = None
    for attempt in range(max_retries):
        try:
            df = yf.Ticker(ticker.upper()).revenue_estimate
            break
        except Exception as e:
            rate_limited = 'RateLimit' in type(e).__name__
            if not rate_limited:
                note(f'{type(e).__name__}: {e}')
                return None
            if attempt >= max_retries - 1:
                note(f'rate limited by Yahoo after {max_retries} attempts — '
                     'revenue left unfilled, not absent')
                return None
            time.sleep(2 ** attempt * 3 + random.uniform(0, 1))

    if df is None or getattr(df, 'empty', True):
        note('no revenue estimate published for this ticker')
        return None
    if '0y' not in df.index or '+1y' not in df.index or 'avg' not in df.columns:
        note('revenue estimate table missing the 0y/+1y avg cells')
        return None

    current = _as_float(df.loc['0y', 'avg'])
    nxt = _as_float(df.loc['+1y', 'avg'])
    if current is None or nxt is None:
        note('0y/+1y revenue average is blank')
        return None
    return {'current': current, 'next': nxt}


def _as_float(value):
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None  # drop NaN


# ---------------------------------------------------------------------------
# Rebasing
# ---------------------------------------------------------------------------

def _rebase(sa_anchor, src_anchor, src_value):
    """
    Carry the fallback source's change onto the StockAnalysis level.

    Multiplicative when both anchors are positive (preserves growth rate for
    compounding series); additive otherwise, since a ratio through zero or a
    sign flip produces nonsense for loss-making years.

    Returns ``(value, method)`` or ``(None, None)`` when it can't be computed.
    """
    if sa_anchor is None or src_anchor is None or src_value is None:
        return None, None

    if sa_anchor > 0 and src_anchor > 0:
        return sa_anchor * (src_value / src_anchor), 'ratio'
    return sa_anchor + (src_value - src_anchor), 'delta'


def _anchor_index(values):
    """Index of the last known value — the column the fallback rebases onto."""
    for i in range(len(values) - 1, -1, -1):
        if values[i] is not None:
            return i
    return None


# ---------------------------------------------------------------------------
# Column matching
# ---------------------------------------------------------------------------

def _column_end_dates(forecast_data, years):
    """
    Map each ``forecast_ranges`` year label to its true fiscal end date.

    Prefers the ``Period Ending`` row of the annual forecast table (real dates
    like 'Sep 30, 2027'), falling back to Dec-31 of the label year. The fallback
    is only safe for calendar-year filers, so it is reported separately and the
    caller downgrades matching to year-only when it is used.
    """
    af = forecast_data.get('annual_forecast') or {}
    periods = af.get('periods') or []
    endings = af.get('period_endings') or []

    by_year = {}
    # Pairing a short endings list against periods positionally would shift every
    # fiscal year by one and silently attach the wrong consensus to each column,
    # so an uneven table is discarded rather than zipped.
    if len(periods) == len(endings):
        for period, ending in zip(periods, endings):
            year = _year_of(period)
            end_date = _parse_full_date(ending)
            if year is not None and end_date is not None:
                by_year[year] = end_date

    exact = {}
    approximate = {}
    for label in years:
        year = _year_of(label)
        if year is None:
            continue
        if year in by_year:
            exact[label] = by_year[year]
        else:
            approximate[label] = date(year, 12, 31)

    return exact, approximate


def _match_rows_to_years(years, column_dates, approx_dates, rows, notes=None):
    """
    Pair each year label with the fallback row covering the same fiscal year.

    Columns with a real period-ending date match on date proximity, tolerant of
    52/53-week drift. Columns without one fall back to matching the calendar
    year of the fiscal end — correct for a Dec-31 filer, but it cannot catch an
    issuer whose FY label differs from the year its fiscal year ends in, so the
    guess is recorded in ``notes``.
    """
    matched = {}
    guessed = []

    for label in years:
        end_date = column_dates.get(label)
        if end_date is not None:
            best = min(rows, key=lambda r: abs((r['end_date'] - end_date).days))
            if abs((best['end_date'] - end_date).days) <= _MATCH_TOLERANCE_DAYS:
                matched[label] = best
            continue

        approx = approx_dates.get(label)
        if approx is None:
            continue
        same_year = [r for r in rows if r['end_date'].year == approx.year]
        if len(same_year) == 1:
            matched[label] = same_year[0]
            guessed.append(label)

    if guessed and notes is not None:
        notes.append(
            'no period-ending dates on the StockAnalysis table; matched '
            + ', '.join(guessed)
            + ' by fiscal-end calendar year (wrong if the issuer labels its FY '
              'off the ending year)'
        )

    return matched


# ---------------------------------------------------------------------------
# Range-table filling
# ---------------------------------------------------------------------------

def _fill_range(rng, matched, source_name, notes, field_label, allow_unanchored=False):
    """
    Fill None entries of a range table's avg/high/low from matched rows.

    ``allow_unanchored`` takes the source's values verbatim when no year
    overlaps StockAnalysis's known column. That skips the level calibration
    that keeps ``next_year_eps`` compatible with the PE median, so it is off by
    default; every such cell is marked ``method='raw'``.

    Returns the list of per-year fill records.
    """
    years = rng.get('years') or []
    filled = []

    for stat in ('avg', 'high', 'low'):
        values = rng.get(stat)
        if not values or len(values) != len(years):
            continue

        anchor_idx = _anchor_index(values)
        if anchor_idx is None:
            notes.append(f"{field_label}/{stat}: no known column to anchor on, skipped")
            continue

        anchor_row = matched.get(years[anchor_idx])
        if anchor_row is None:
            # Happens every year around a fiscal-year rollover: the year just
            # ended, so the source has dropped it while StockAnalysis still
            # shows its stale estimate. Later years exist on the source but
            # there is no overlapping year to calibrate the level against.
            if not allow_unanchored:
                notes.append(
                    f"{field_label}/{stat}: {source_name} has dropped anchor year "
                    f"{years[anchor_idx]} (fiscal year already ended), so its "
                    f"later years cannot be calibrated — left empty. Pass "
                    f"allow_unanchored=True to take them raw instead."
                )
                continue
            notes.append(
                f"{field_label}/{stat}: no overlapping year to calibrate against; "
                f"took {source_name} values RAW — the EPS definition may differ "
                f"from StockAnalysis, so do not trust the level"
            )

        sa_anchor = values[anchor_idx]
        src_anchor = anchor_row.get(stat) if anchor_row else None

        for i, label in enumerate(years):
            if values[i] is not None:
                continue
            row = matched.get(label)
            if row is None:
                if stat == 'avg':
                    notes.append(
                        f"{field_label}: {source_name} has no estimate for "
                        f"{label}, left empty"
                    )
                continue

            if anchor_row is None:
                value, method = row.get(stat), 'raw'
            else:
                value, method = _rebase(sa_anchor, src_anchor, row.get(stat))
            if value is None:
                continue

            values[i] = round(value, 4)
            if stat == 'avg':
                num = row.get('num_estimates')
                filled.append({
                    'year': label,
                    'field': field_label,
                    'value': values[i],
                    'raw': row.get(stat),
                    'method': method,
                    # Nothing was calibrated for a raw fill, so claiming an
                    # anchor year would misrepresent where the level came from.
                    'anchor_year': None if method == 'raw' else years[anchor_idx],
                    'source_fiscal_end': row.get('fiscal_end'),
                    'num_estimates': num,
                    'thin_coverage': bool(num is not None and num < _THIN_COVERAGE),
                })

    rng['source'] = [
        source_name if any(f['year'] == label for f in filled) else 'stockanalysis'
        for label in years
    ]
    return filled


def _fill_growth(growth_rng, level_rng):
    """
    Derive missing growth percentages from the now-filled level series.

    Every band is measured against the previous year's *average*, not against
    its own band — that is StockAnalysis's convention. CGNX FY2026 shows
    high/avg/low EPS of 1.66/1.48/1.41 against growths of 62.7/45.5/37.8%,
    which all resolve to the same 1.02 base (the FY2025 actual). Dividing high
    by the prior high instead would invert the band ordering.
    """
    if not growth_rng or not level_rng:
        return
    years = growth_rng.get('years') or []
    bases = level_rng.get('avg') or []
    if len(bases) != len(years):
        return

    for stat in ('avg', 'high', 'low'):
        growth = growth_rng.get(stat)
        levels = level_rng.get(stat)
        if not growth or not levels or len(growth) != len(years) or len(levels) != len(years):
            continue
        for i in range(1, len(years)):
            if growth[i] is not None:
                continue
            base, cur = bases[i - 1], levels[i]
            if base is None or cur is None or base <= 0:
                continue
            growth[i] = round((cur / base - 1) * 100, 2)


def _fill_annual_forecast(forecast_data, field, values_by_year):
    """Mirror filled values into the long ``annual_forecast`` timeline."""
    af = forecast_data.get('annual_forecast')
    if not af:
        return
    periods = af.get('periods') or []
    series = af.get(field)
    if not series or len(series) != len(periods):
        return

    for i, period in enumerate(periods):
        if series[i] is not None:
            continue
        year = _year_of(period)
        if year is None:
            continue
        value = values_by_year.get(year)
        if value is not None:
            series[i] = value


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def apply_forecast_fallback(forecast_data, use_nasdaq=True, use_yfinance=True,
                            allow_unanchored=False):
    """
    Fill Pro-locked forecast years in place and return a report dict.

    Only ``None`` cells are touched — anything StockAnalysis served stays
    authoritative. The report is also stored on ``forecast_data['fallback']``
    so every filled number stays auditable in the saved JSON.

    ``allow_unanchored`` covers the fiscal-year-rollover case where the source
    has already dropped the one year StockAnalysis still shows, leaving nothing
    to calibrate against. Off by default: an uncalibrated EPS multiplied by a
    StockAnalysis-derived PE median is exactly the error rebasing exists to
    prevent.

    Callers must re-run their ``_populate_legacy_annual`` afterwards to push the
    newly filled years into the ``annual`` dict the database reads.
    """
    report = {
        'applied': False,
        'sources': {},
        'filled': [],
        'notes': [],
    }
    forecast_data['fallback'] = report

    ticker = forecast_data.get('ticker')
    ranges = forecast_data.get('forecast_ranges') or {}
    notes = report['notes']

    if not ticker:
        notes.append('no ticker on forecast_data, nothing to do')
        return report

    # --- EPS from Nasdaq ---------------------------------------------------
    eps_rng = ranges.get('eps')
    if use_nasdaq and eps_rng and any(v is None for v in (eps_rng.get('avg') or [])):
        rows = fetch_nasdaq_yearly_eps(ticker)
        if not rows:
            notes.append('nasdaq: no annual forecast returned')
        else:
            years = eps_rng.get('years') or []
            exact, approx = _column_end_dates(forecast_data, years)
            matched = _match_rows_to_years(years, exact, approx, rows, notes)
            if not matched:
                notes.append('nasdaq: no fiscal year lined up with the StockAnalysis columns')
            else:
                filled = _fill_range(eps_rng, matched, 'nasdaq', notes, 'eps',
                                     allow_unanchored=allow_unanchored)
                if filled:
                    report['applied'] = True
                    report['sources']['eps'] = 'nasdaq'
                    report['filled'].extend(filled)
                    _fill_growth(ranges.get('eps_growth'), eps_rng)
                    _fill_annual_forecast(
                        forecast_data, 'eps',
                        {_year_of(f['year']): f['value'] for f in filled},
                    )
                    _fill_annual_forecast(
                        forecast_data, 'eps_growth',
                        _growth_by_year(ranges.get('eps_growth')),
                    )
                report['nasdaq_raw'] = [
                    {k: v for k, v in row.items() if k != 'end_date'} for row in rows
                ]

    # --- Revenue from yfinance --------------------------------------------
    rev_rng = ranges.get('revenue')
    if use_yfinance and rev_rng and any(v is None for v in (rev_rng.get('avg') or [])):
        est = fetch_yf_revenue_estimate(ticker, notes=notes)
        if est:
            values = rev_rng.get('avg') or []
            years = rev_rng.get('years') or []
            anchor_idx = _anchor_index(values)
            # Yahoo's 0y is the fiscal year in progress, which is the same year
            # StockAnalysis still shows for free — so it only lines up when the
            # anchor is the first column and exactly one year follows it.
            if anchor_idx is None or anchor_idx + 1 >= len(values):
                notes.append('yfinance: no locked revenue year immediately after the anchor')
            elif values[anchor_idx + 1] is not None:
                notes.append('yfinance: next revenue year already populated')
            else:
                value, method = _rebase(values[anchor_idx], est['current'], est['next'])
                if value is None:
                    notes.append('yfinance: could not rebase revenue onto the anchor')
                else:
                    target_year = years[anchor_idx + 1]
                    values[anchor_idx + 1] = round(value, 2)
                    rev_rng['source'] = [
                        'yfinance' if i == anchor_idx + 1 else 'stockanalysis'
                        for i in range(len(years))
                    ]
                    report['applied'] = True
                    report['sources']['revenue'] = 'yfinance'
                    report['filled'].append({
                        'year': target_year,
                        'field': 'revenue',
                        'value': values[anchor_idx + 1],
                        'raw': est['next'],
                        'method': method,
                        'anchor_year': years[anchor_idx],
                        'source_fiscal_end': 'yahoo +1y',
                        'num_estimates': None,
                        'thin_coverage': False,
                    })
                    _fill_growth(ranges.get('revenue_growth'), rev_rng)
                    _fill_annual_forecast(
                        forecast_data, 'revenue',
                        {_year_of(target_year): values[anchor_idx + 1]},
                    )
                    _fill_annual_forecast(
                        forecast_data, 'revenue_growth',
                        _growth_by_year(ranges.get('revenue_growth')),
                    )

    return report


def _growth_by_year(growth_rng):
    """{year_int: avg growth} for a range table, skipping unknown cells."""
    if not growth_rng:
        return {}
    years = growth_rng.get('years') or []
    values = growth_rng.get('avg') or []
    out = {}
    for label, value in zip(years, values):
        year = _year_of(label)
        if year is not None and value is not None:
            out[year] = value
    return out
