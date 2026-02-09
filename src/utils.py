import random
import re
import time
from io import StringIO
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.exceptions import HTTPError, RequestException
from tqdm import tqdm  # Optional: For progress bars

def clean_json_data(data_string):
    """Clean and prepare string data for JSON parsing in getting quarterly forecast data."""
    # Remove [PRO] and undefined
    data_string = data_string.replace("[PRO]", 'null')
    data_string = data_string.replace("undefined", "null").strip()

    # Fix decimal formatting
    data_string = re.sub(r'(?<!\d)(-\.)(\d+)', r'-0.\2', data_string)  # Convert -.X to -0.X
    data_string = re.sub(r'(?<!\d)(\.)(\d+)', r'0.\2', data_string)    # Convert .X to 0.X

    # Add quotes around property names (JavaScript object -> JSON)
    data_string = re.sub(r'(\w+):', r'"\1":', data_string)

    # Wrap in curly braces if not already wrapped
    if not data_string.strip().startswith('{'):
        data_string = '{' + data_string + '}'

    return data_string

def clean_soup_value(soup):
    """Clean and convert the value to a numeric type if applicable in Stock analysis web."""
    clean_soup = soup.split('const element = ')[1].split(";")[0]
    data_string = clean_soup.replace("void 0", "null")
    data_string = re.sub(r'([{,]\s*)([a-zA-Z_$][a-zA-Z0-9_$]*)(:)', r'\1"\2"\3', data_string)
    data_string = data_string.replace("'", '"')
    return data_string

def fetch_url(url, headers, max_retries=3, timeout=10, sleep_between_retries=2):
    """
    Fetches the content of a URL with retries.

    Args:
        url (str): The URL to fetch.
        headers (dict): HTTP headers to include in the request.
        max_retries (int): Maximum number of retry attempts.
        timeout (int): Timeout for the HTTP request.
        sleep_between_retries (int): Seconds to wait between retries.

    Returns:
        requests.Response or None: The HTTP response if successful, else None.
    """
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response
        except RequestException as e:
            print(f"Attempt {attempt + 1} failed for URL: {url}. Error: {e}")
            if attempt < max_retries - 1:
                time.sleep(sleep_between_retries)
            else:
                print(f"Failed to fetch data for {url} after {max_retries} attempts.")
                return None

def parse_html(content):
    """
    Parses HTML content using BeautifulSoup.

    Args:
        content (str): HTML content as a string.

    Returns:
        BeautifulSoup: Parsed HTML.
    """
    return BeautifulSoup(content, 'html.parser')

def extract_percentage(text):
    """
    Extracts percentage value from text and converts to float.

    Args:
        text (str): Text containing percentage

    Returns:
        float: Percentage value as float, or None if not found
    """
    if not text:
        return None
    match = re.search(r'([-+]?\d*\.?\d+)%', text)
    if match:
        return float(match.group(1))
    return None


def compute_iqr_statistics(pe_array: np.ndarray) -> Tuple[float, float, float]:
    """
    Compute Q1, Q3, and IQR for the given PE ratios.

    Args:
        pe_array (np.ndarray): Array of PE ratios.

    Returns:
        Tuple[float, float, float]: Q1, Q3, and IQR values.
    """
    q1 = np.percentile(pe_array, 25)
    q3 = np.percentile(pe_array, 75)
    iqr = q3 - q1
    return q1, q3, iqr

def filter_outliers(pe_array: np.ndarray, q1: float, q3: float, iqr: float) -> np.ndarray:
    """
    Filter out outliers based on IQR.

    Args:
        pe_array (np.ndarray): Array of PE ratios.
        q1 (float): First quartile.
        q3 (float): Third quartile.
        iqr (float): Interquartile range.

    Returns:
        np.ndarray: Filtered array without outliers.
    """
    lower_bound = q1 - 1.5 * iqr
    upper_bound = q3 + 1.5 * iqr
    return pe_array[(pe_array >= lower_bound) & (pe_array <= upper_bound)]
