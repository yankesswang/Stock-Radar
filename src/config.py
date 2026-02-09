ratio_names = [
    "marketcap", "enterpriseValue",
    "earningsdate", "exdivdate",
    "sharesOutClass", "sharesout", "sharesgrowthyoy", "sharesgrowthqoq", "sharesInsiders", "sharesInstitutions", "float",
    "pe", "peForward", "ps", "psForward", "pb", "ptbvRatio", "pfcf", "pocf", "pegRatio",
    "evEarnings", "evSales", "evEbitda", "evEbit", "evFcf",
    "currentRatio", "quickRatio", "debtEquity", "debtEbitda", "debtFcf", "interestCoverage",
    "roe", "roa", "roic", "roce", "revPerEmployee", "profitPerEmployee", "employees", "assetturnover", "inventoryturnover",
    "taxexp", "taxrate",
    "beta", "ch1y", "sma50", "sma200", "rsi", "averageVolume",
    "shortInterest", "shortPriorMonth", "shortShares", "shortFloat", "shortRatio",
    "revenue", "gp", "opinc", "pretax", "netinc", "ebitda", "ebit", "eps",
    "totalcash", "debt", "netcash", "netcashpershare", "equity", "bvps", "workingcapital",
    "ncfo", "capex", "fcf", "fcfps",
    "grossMargin", "operatingMargin", "pretaxMargin", "profitMargin", "ebitdaMargin", "ebitMargin", "fcfMargin",
    "dps", "dividendYield", "dividendGrowth", "dividendGrowthYears", "payoutRatio", "buybackYield", "totalReturn", "earningsYield", "fcfYield",
    "priceTarget", "priceTargetChange", "analystRatings", "analystCount", "revenue5y", "eps5y",
    "lastSplitDate", "lastSplitType", "splitRatio",
    "zScore", "fScore"
]

forecast_names = [
    "action_rt",
    "analyst",
    "analysts",
    "average",
    "buy",
    "chart",
    "consensus",
    "count",
    "date",
    "dates",
    "eps",
    "epsGrowth",
    "epsNext",
    "epsThis",
    "filtered",
    "firm",
    "fiscalQuarter",
    "fiscalYear",
    "growth",
    "high",
    "hold",
    "low",
    "median",
    "month",
    "pt_now",
    "pt_old",
    "rating_new",
    "rating_old",
    "revenue",
    "revenueGrowth",
    "revenueNext",
    "revenueThis",
    "score",
    "scores",
    "sell",
    "slug",
    "strongBuy",
    "strongSell",
    "time",
    "total",
    "updated"
]


STOCK_LIST = {
        "大科技": {"GOOG", "META", "AMZN", "NFLX", "AAPL", "MSFT", "TSLA", "ADBE"},
        "航空郵輪": {"AAL", "LUV", "DAL", "UAL", "ALK", "BA", "CCL", "RCL"},
        "銀行": {"BAC", "JPM", "GS", "C", "WFC", 'NU', 'SOFI'},
        "傳統": {"DIS", "ISRG", "UNH", "ABBV", "CVS", 'TRV', "JNJ","PFE","MRK","LLY","AMGN"},
        "支付": {"MA", "V", "PYPL", "AXP"},
        "零售": {"LULU", "COST", "PG", "KR", "JWN", "NKE", "DG", "FL", "EL", "PVH", "TPR"},
        "食品": {"HIMS", "CELH","TSN", "MNST", "MCD", "SBUX", "KO", "PEP", "CMG", "YUM", 'DPZ', 'CAKE', 'JACK', 'PLAY', 'FIZZ', 'BLMN', 'DENN', 'DIN'},
        "半導體": {'NVDA', "TSM", "AMD", "QCOM", "MU", "INTC", "ASML", "SWKS", "QRVO", "AVGO", "AMAT", 'MRVL', 'ARM', 'CLS', 'DELL',"ORCL"},
        "原油": {"CVX", "VLO", "COP", "XOM", "OXY",},
        "旅遊": {"BKNG", "EXPE", "MAR", "HLT", "ABNB", "H", "WYNN", "IHG", "LVS", "MGM"},
        "工業": {'NEE', "DE", "HD", "APD", "CAT", "ETN", "HON", "WM", "GE", "MMM", "SUM", "X", 'ENPH', 'SEDG', "FSLR", "OKLO", "ETN", "BE"},
        'SaaS': {'SAP', 'CFLT', 'ACN', 'BSX', 'SHOP', 'CRM', 'DDOG', 'NOW', 'INTU', 'SQ','SNOW', 'MDB', 'OKTA', 'ADSK', 'TTD', 'INOD', "FISV"},
        '軟體':   {
            "U", "RBLX",   "SNAP",   "PINS", "Z",   "TTD",    "APP",   "ETSY", "RDDT","DASH",      "MTCH",   "EXPE", "SPLK",
             "OKTA",   "PATH","IBM","CSCO"
        },

        "記憶體":{"MU","WDC", "PSTG", "SNDK", "STX", "BLZE", "NTAP"},
        "資安":{
             "PANW", "CRWD", "CYBR","NET","ZS","DOCU","FTNT","S","DDOG"
        },
        "ai半導體": {
            "FN", "LITE", "CLS", "TTMI", "ANET", "ALAB"
        },
        "機器人": {
            "SYM", "ROK", "TER", "ADI", "PTC", "OUST", "AEVA", "LSCC", "SERV","AUR","RR"
        },
        "成長股": {
            "UBER", "HIMS", "OSCR","MELI","NU","TMDX","SPOT"
        }

}
PE_TICKER_TO_COMPANY = {
    # 大科技
    "goog": "alphabet",
    "meta": "meta-platforms",
    "amzn": "amazon",
    "nflx": "netflix",
    "aapl": "apple",
    "msft": "microsoft",
    "tsla": "tesla",
    "adbe": "adobe",

    # 航空郵輪
    "aal": "american-airlines",
    "luv": "southwest-airlines",
    "dal": "delta-air-lines",
    "ual": "united-airlines",
    "alk": "alaska-air-group",
    "ba": "boeing",
    "eadsy": "airbus",
    "ccl": "carnival",
    "rcl": "royal-caribbean",

    # 銀行
    "bac": "bank-of-america",
    "jpm": "jpmorgan-chase",
    "gs": "goldman-sachs",
    "c": "citigroup",
    "wfc": "wells-fargo",
    "blk": "blackrock",
    "nu": "nu-holdings",
    "sofi": "sofi",

    # 傳統
    "dis": "walt-disney",
    "isrg": "intuitive-surgical",
    "unh": "unitedhealth-group",
    "abbv": "abbvie",
    "cvs": "cvs-health",
    "trv": "the-travelers-companies",

    # 支付
    "ma": "mastercard",
    "v": "visa",
    "pypl": "paypal",
    "axp": "american-express",

    # 零售
    "lulu": "lululemon-athletica",
    "gps": "gap",
    "cost": "costco-wholesale",
    "pg": "procter-&-gamble",
    "kr": "kroger",
    "jwn": "nordstrom",
    "nke": "nike",
    "dg": "dollar-general",
    "fl": "foot-locker",
    "lvmhf": "lvmh",
    "el": "estée-lauder",
    "pvh": "pvh-corp.",
    "tpr": "tapestry",

    # 食品
    "tsn": "tyson-foods",
    "mcd": "mcdonald's",
    "sbux": "starbucks",
    "ko": "coca-cola",
    "pep": "pepsi-co",
    "cmg": "chipotle-mexican-grill",
    "yum": "yum!-brands",
    "dpz": "domino's-pizza",
    "cake": "the-cheesecake-factory",
    "jack": "jack-in-the-box",
    "play": "dave-&-busters",
    "fizz": "national-beverage",
    "blmn": "bloomin'-brands",
    "denn": "denny's",
    "din": "dine-brands-global",

    # 半導體
    "nvda": "nvidia",
    "tsm": "taiwan-semiconductor-(tsmc)",
    "amd": "advanced-micro-devices",
    "qcom": "qualcomm",
    "mu": "micron-technology",
    "intc": "intel",
    "asml": "asml-holding",
    "swks": "skyworks-solutions",
    "qrvo": "qorvo",
    "avgo": "broadcom",
    "amat": "applied-materials",
    "mrvl": "marvell-technology",
    "arm": "arm-holdings",
    "cls": "celestica",
    "dell": "dell-technologies",
    "hpe": "hewlett-packard-enterprise",

    # 原油
    "cvx": "chevron",
    "vlo": "valero-energy",
    "cop": "conocophillips",
    "xom": "exxon-mobil",
    "oxy": "occidental-petroleum",
    "cpe": "callon-petroleum",
    "enb": "enbridge",

    # 旅遊
    "bkng": "booking-holdings",
    "expe": "expedia-group",
    "mar": "marriott-international",
    "hlt": "hilton-worldwide",
    "abnb": "airbnb",
    "h": "hyatt-hotels",
    "wynn": "wynn-resorts",
    "ihg": "intercontinental-hotels",
    "lvs": "las-vegas-sands",
    "mgm": "mgm-resorts",

    # 工業
    "nee": "nextera-energy",
    "de": "deere-&-company",
    "hd": "home-depot",
    "apd": "air-products-&-chemicals",
    "cat": "caterpillar",
    "etn": "eaton-corporation",
    "hon": "honeywell",
    "wm": "waste-management",
    "ge": "general-electric",
    "mmm": "3m-company",
    "sum": "summit-materials",
    "x": "u.s.-steel",
    "enph": "enphase-energy",
    "sedg": "solaredge",
    "fslr": "first-solar",

    # SaaS
    "sap": "sap-se",           # Ticker is sometimes just 'sap'
    "cflt": "confluent",
    "acn": "accenture",
    "bsx": "boston-scientific",
    "shop": "shopify",
    "crm": "salesforce",
    "ddog": "datadog",
    "now": "servicenow",
    "intu": "intuit",
    "sq": "block-(square)",
    "wdays": "workday",
    "snow": "snowflake",
    "mdb": "mongodb",
    "okta": "okta",
    "adsk": "autodesk",
    "ttd": "the-trade-desk",

    "amgn": "amgen",
    "jnj": "johnson-&-johnson",
    "lly": "eli-lilly",
    "mrk": "merck-&-co",
    "pfe": "pfizer",

    # 食品 / 飲料
    "celh": "celsius-holdings",
    "hims": "hims-&-hers-health",
    "mnst": "monster-beverage",

    # 半導體（這裡你把 ORCL 放在半導體籃子裡）
    "orcl": "oracle",

    # 工業 / 能源科技
    "be": "bloom-energy",
    "oklo": "oklo",

    # SaaS
    "fisv": "fiserv",
    "inod": "innodata",

    # 軟體 / 網路
    "app": "applovin",
    "csco": "cisco-systems",
    "dash": "doordash",
    "etsy": "etsy",
    "ibm": "ibm",
    "mtch": "match-group",
    "path": "uipath",
    "pins": "pinterest",
    "rblx": "roblox",
    "rddt": "reddit",
    "snap": "snap",
    "splk": "splunk",
    "u": "unity-software",
    "z": "zillow-group",

    # 記憶體 / 儲存
    "blze": "backblaze",
    "ntap": "netapp",
    "pstg": "pure-storage",
    "sndk": "sandisk",
    "stx": "seagate-technology",
    "wdc": "western-digital",

    # 資安
    "crwd": "crowdstrike",
    "cybr": "cyberark-software",
    "docu": "docusign",
    "ftnt": "fortinet",
    "net": "cloudflare",
    "panw": "palo-alto-networks",
    "s": "sentinelone",
    "zs": "zscaler",

    # AI 半導體 / 基礎設施
    "alab": "astera-labs",
    "anet": "arista-networks",
    "fn": "fabrinet",
    "lite": "lumentum",
    "ttmi": "ttm-technologies",

    # 機器人 / 自動化
    "adi": "analog-devices",
    "aeva": "aeva-technologies",
    "aur": "aurora-innovation",
    "lscc": "lattice-semiconductor",
    "oust": "ouster",
    "ptc": "ptc",
    "rok": "rockwell-automation",
    "rr": "rolls-royce-holdings",
    "serv": "serve-robotics",
    "sym": "symbotic",
    "ter": "teradyne",

    # 成長股
    "meli": "mercadolibre",
    "oscr": "oscar-health",
    "spot": "spotify",
    "tmdx": "transmedics-group",
    "uber": "uber-technologies",
}
