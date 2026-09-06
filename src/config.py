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
    "大科技": {
        "AAPL", "ADBE", "AMZN", "GOOG", "GOOGL", "META", "MSFT", "NFLX", "PLTR", "TSLA"
    },
    "航空郵輪": {
        "AAL", "ALGT", "ALK", "BA", "CCL", "CPA", "DAL", "JBLU", "LUV", "RCL", "RYAAY", "SKYW",
        "UAL", "ULCC"
    },
    "銀行": {
        "ALLY", "BAC", "C", "CFG", "COF", "EWBC", "FITB", "GS", "HBAN", "JPM", "KEY", "MTB",
        "NU", "PNC", "RF", "SOFI", "TFC", "USB", "WAL", "WFC", "ZION"
    },
    "傳統": {
        "ABBV", "AMGN", "CVS", "DIS", "ISRG", "JNJ", "LLY", "MRK", "PFE", "TRV", "UNH"
    },
    "支付": {
        "AXP", "DLO", "FLYW", "FOUR", "GPN", "MA", "MQ", "PYPL", "TOST", "V", "WEX"
    },
    # JWN 2025-05-21 私有化下市、FL 2025-09-08 併入 DKS，皆已移除
    "零售": {
        "AEO", "ANF", "AZO", "BBY", "BIRK", "BURL", "CHWY", "COST", "CROX", "DECK", "DG",
        "DKS", "EL", "FIVE", "GAP", "KR", "LULU", "NKE", "ONON", "ORLY", "PG", "PVH", "RH",
        "ROST", "TGT", "TJX", "TPR", "TSCO", "ULTA", "VFC", "W", "WMT", "WSM"
    },
    # DENN 2026-01-16 由 TriArtisan 財團私有化下市，已移除
    "食品": {
        "BLMN", "BROS", "CAG", "CAKE", "CAVA", "CELH", "CMG", "DIN", "DPZ", "DRI", "EAT",
        "FIZZ", "GIS", "HIMS", "HRL", "HSY", "JACK", "KDP", "KHC", "KO", "LOCO", "LW", "MCD",
        "MNST", "PEP", "PLAY", "PTLO", "QSR", "SBUX", "SG", "SHAK", "SJM", "STZ", "TAP", "TSN",
        "TXRH", "WEN", "WING", "YUM"
    },
    "半導體": {
        "ACLS", "AMAT", "AMD", "AMKR", "ARM", "ASML", "ASX", "AVGO", "CAMT", "CDNS", "CLS",
        "COHR", "DELL", "DIOD", "ENTG", "GFS", "INTC", "KLAC", "KLIC", "LRCX", "LSCC", "MCHP",
        "MRVL", "MTSI", "MU", "NVDA", "NVMI", "NXPI", "ON", "ONTO", "ORCL", "POWI", "QCOM",
        "QRVO", "SITM", "SNPS", "SWKS", "TER", "TSM", "UMC", "WOLF"
    },
    "原油": {
        "AR", "BKR", "COP", "CVX", "DVN", "EOG", "EQT", "FANG", "HAL", "KMI", "LNG", "MPC",
        "OKE", "OXY", "PSX", "RRC", "SLB", "TRGP", "VLO", "WMB", "XOM"
    },
    "旅遊": {
        "ABNB", "BKNG", "CZR", "DKNG", "EXPE", "FLUT", "H", "HLT", "IHG", "LVS", "MAR", "MGM",
        "NCLH", "PLNT", "TRIP", "VIK", "WYNN"
    },
    # SUM 2025-02-10 併入 Quikrete、X 2025-06 由 Nippon Steel 收購下市，皆已移除
    "工業": {
        "ACM", "AME", "APD", "ATKR", "BE", "CARR", "CAT", "CMI", "DE", "DOV", "DY", "EME",
        "EMR", "ENPH", "ETN", "FAST", "FLEX", "FSLR", "GE", "GNRC", "GWW", "HD", "HON", "IR",
        "ITW", "J", "JCI", "LII", "LYB", "MMM", "MTZ", "NEE", "OKLO", "OTIS", "PCAR", "PH",
        "PNR", "POWL", "ROP", "SEDG", "STRL", "TT", "URI", "VIAV", "VRT", "WM", "XYL"
    },
    # 下市/改名（2026-08-03 核對）：CFLT 查無報價已移除；SQ 已改代號為 XYZ（Block, Inc.）
    "SaaS": {
        "ACN", "ADSK", "AI", "APPF", "ASAN", "BILL", "BRZE", "BSX", "CRM", "DBX", "DDOG",
        "ESTC", "FISV", "FROG", "GTLB", "GTM", "HUBS", "INOD", "INTU", "MDB", "MNDY", "NCNO",
        "NOW", "OKTA", "PCOR", "SAP", "SHOP", "SNOW", "SOUN", "TEAM", "TTD", "TWLO", "VEEV",
        "WIX", "XYZ", "ZM"
    },
    # SPLK 查無報價已移除（併入 CSCO，已在本籃）
    "軟體": {
        "AFRM", "APP", "BKSY", "BMBL", "CARG", "COMP", "CSCO", "CVNA", "DASH", "DUOL", "EBAY",
        "ETSY", "GRND", "HOOD", "IBM", "LYFT", "MTCH", "OPEN", "PATH", "PINS", "RBLX", "RDDT",
        "SE", "SNAP", "TDOC", "U", "YELP", "Z", "ZG"
    },
    # PSTG 2026-04-17 隨 Pure Storage → Everpure 改名，代號改為 P
    "記憶體": {
        "BLZE", "NTAP", "P", "SNDK", "STX", "WDC"
    },
    # CYBR 查無報價已移除（併入 PANW，已在本籃）
    "資安": {
        "AKAM", "CHKP", "CRWD", "DOCU", "FTNT", "GEN", "NET", "PANW", "QLYS", "RBRK", "S",
        "SAIL", "TENB", "VRNS", "ZS"
    },
    "ai半導體": {
        "ALAB", "ANET", "APH", "CIEN", "CRDO", "CRWV", "FN", "LITE", "MOD", "NBIS", "SMCI",
        "TTMI"
    },
    "機器人": {
        "ADI", "AEVA", "ALIT", "AUR", "CGNX", "INDI", "KRNT", "MBLY", "NDSN", "NVTS", "OUST",
        "PTC", "ROK", "RR", "SERV", "SYM"
    },
    "成長股": {
        "ACHR", "ASTS", "BWXT", "INSP", "IONQ", "JOBY", "LEU", "LUNR", "MELI", "NNE", "OSCR",
        "QBTS", "RDW", "RGTI", "SMR", "SPOT", "TMC", "TMDX", "UBER"
    },
    "ETF": {
        "ARKK", "BITO", "GDX", "GLD", "HYG", "IGV", "ITA", "IWM", "JETS", "KRE", "NVDL", "QQQ",
        "SLV", "SMH", "SOXX", "SPY", "TAN", "TLT", "TSLL", "URA", "UUP", "VT", "XBI", "XLB",
        "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY", "XOP"
    },
    # ── 2026-09-06 新增：原本整片掃不到的產業 ──
    "國防航太": {
        "AVAV", "AXON", "CW", "GD", "HEI", "HWM", "KTOS", "LDOS", "LHX", "LMT", "NOC", "RKLB",
        "RTX", "TDG"
    },
    "電力公用": {
        "AEP", "AES", "CEG", "D", "DUK", "ED", "EIX", "ETR", "EXC", "GEV", "NRG", "PEG", "PWR",
        "SO", "SRE", "TLN", "TXNM", "VST", "XEL"
    },
    "REIT不動產": {
        "AMT", "AVB", "CCI", "DLR", "EQIX", "EQR", "EXR", "IRM", "O", "PLD", "PSA", "SBAC",
        "SPG", "VICI", "WELL"
    },
    "原物料礦業": {
        "AA", "AEM", "ALB", "CCJ", "CLF", "DD", "FCX", "GOLD", "IP", "LIN", "MLM", "MP", "NEM",
        "NUE", "SCCO", "SHW", "STLD", "TECK", "UEC", "VMC"
    },
    "券商資管": {
        "AMP", "APO", "BLK", "BX", "CME", "COIN", "IBKR", "ICE", "KKR", "MCO", "MS", "NDAQ",
        "RJF", "SCHW", "SPGI", "TROW"
    },
    "保險": {
        "ACGL", "AFL", "AIG", "ALL", "CB", "CINF", "EG", "HIG", "LNC", "MET", "PGR", "PRU",
        "ROOT", "UNM", "WRB"
    },
    "加密": {
        "CIFR", "CLSK", "CORZ", "GLXY", "HUT", "MARA", "MSTR", "RIOT", "WULF"
    },
    "生技醫藥": {
        "A", "ALNY", "BIIB", "BMY", "BNTX", "CRSP", "DHR", "DXCM", "EW", "GEHC", "GILD",
        "IDXX", "INCY", "MDT", "MRNA", "NBIX", "NTRA", "PODD", "REGN", "RMD", "SRPT", "SYK",
        "TMO", "VRTX", "ZTS"
    },
    "中概ADR": {
        "BABA", "BEKE", "BIDU", "CPNG", "FUTU", "GRAB", "JD", "LI", "NIO", "PDD", "TCOM",
        "TME", "XPEV", "YMM"
    },
    "電信媒體": {
        "CHTR", "CMCSA", "EA", "FOXA", "FUBO", "LYV", "NYT", "ROKU", "SIRI", "T", "TMUS",
        "TTWO", "VZ", "WBD"
    },
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
    "nke": "nike",
    "dg": "dollar-general",
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
    "nxpi": "nxp-semiconductors",

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
    "enph": "enphase-energy",
    "sedg": "solaredge",
    "fslr": "first-solar",

    # SaaS
    "sap": "sap-se",           # Ticker is sometimes just 'sap'
    "acn": "accenture",
    "bsx": "boston-scientific",
    "shop": "shopify",
    "crm": "salesforce",
    "ddog": "datadog",
    "now": "servicenow",
    "intu": "intuit",
    "xyz": "block",            # 前 SQ，2025 改代號
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
    "u": "unity-software",
    "z": "zillow-group",

    # 記憶體 / 儲存
    "blze": "backblaze",
    "ntap": "netapp",
    "p": "everpure",           # 前 PSTG（Pure Storage），2026-04 改名改代號
    "sndk": "sandisk",
    "stx": "seagate-technology",
    "wdc": "western-digital",

    # 資安
    "crwd": "crowdstrike",
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
