#
# Stock Info Utils
#

def add_suffix_to_stock_code(stock_code: str) -> str:
    """Add market suffix to stock code.
    Args:
        stock_code (str): Stock code with market prefix, e.g., "sh600519" or "sz000001".

    Returns:
        str: Stock code with market suffix, e.g., "600519.SH" or "000001.SZ".
    """
    code, suffix = stock_code.split('.') if '.' in stock_code else (stock_code, '')
    suffix = suffix.lower()
    if suffix == "sh":
        return f"{code}.SH"
    elif suffix == "sz":
        return f"{code}.SZ"

    market = stock_code[:2].lower()
    code = stock_code[2:]
    if market == "sh":
        return f"{code}.SH"
    elif market == "sz":
        return f"{code}.SZ"
    
    code = stock_code[:6]
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"Invalid stock code: {stock_code}")
    if code.startswith("6"):
        return f"{code}.SH"
    else:
        return f"{code}.SZ"