import re

def convert_akcode_to_tushare(ak_code):
    """
    将 AkShare 的6位数字股票代码转换为 Tushare 格式

    Args:
        ak_code: AkShare 返回的6位数字股票代码

    Returns:
        str: Tushare 格式的股票代码 (如 '000001.SZ')
    """
    if not isinstance(ak_code, str):
        ak_code = str(ak_code)

    if '.' in ak_code:
        return f'{ak_code.split('.')[0]}.{ak_code.split('.')[1].upper()}'

    # 确保是6位数字
    ak_code = ak_code.zfill(6)

    # 根据代码前缀判断交易所
    if ak_code.startswith(('600', '601', '603', '605', '688', '689')):
        return ak_code + '.SH'  # 上海证券交易所
    elif ak_code.startswith(('000', '001', '002', '003', '300', '301')):
        return ak_code + '.SZ'  # 深圳证券交易所
    else:
        # 如果无法识别，默认返回深圳格式
        return ak_code + '.SZ'


class AShareStockCodeValidator:
    """
    Production-ready validator for Chinese A-share stock codes
    """

    def __init__(self):
        self.main_pattern = re.compile(r'^(\d{6})$')

        # Comprehensive market segments
        self.market_segments = {
            'SSE_Main': r'^60[0-9]{4}$',
            'SSE_STAR': r'^688\d{3}$',
            'SZSE_Main': r'^00[0-1]\d{3}$',
            'SZSE_SME': r'^00[2-3]\d{3}$',
            'SZSE_ChiNext': r'^300\d{3}$',
            'SSE_B': r'^900\d{3}$',
            'SZSE_B': r'^200\d{3}$'
        }

        # Well-known stock codes for quick validation
        self.well_known_codes = {
            '000001': 'Ping An Bank',
            '600036': 'China Merchants Bank',
            '601318': 'Ping An Insurance',
            '600519': 'Kweichow Moutai',
            '000858': 'Wuliangye',
            '300750': 'CATL',
            '688981': 'SMIC',
            '002415': 'Hikvision'
        }

    def validate(self, code, validate_known=True):
        """
        Validate A-share stock code

        Args:
            code: Stock code to validate
            validate_known: If True, also check if it's a well-known code
        """
        code_str = str(code).strip()

        # Basic format check
        if not self.main_pattern.match(code_str):
            return False, "Invalid format"

        # Market segment check
        segment = self._get_market_segment(code_str)
        if not segment:
            return False, "Unknown market segment"

        # Optional: Check if it's a well-known code
        if validate_known and code_str in self.well_known_codes:
            company = self.well_known_codes[code_str]
            return True, f"{segment} - {company}"

        return True, segment

    def _get_market_segment(self, code):
        """Get market segment for the code"""
        for segment, pattern in self.market_segments.items():
            if re.match(pattern, code):
                return segment
        return None

    def validate_batch(self, codes):
        """Validate multiple codes at once"""
        results = {}
        for code in codes:
            is_valid, info = self.validate(code)
            results[code] = {
                'valid': is_valid,
                'info': info
            }
        return results

    def generate_regex_pattern(self):
        """Generate the comprehensive regex pattern"""
        patterns = list(self.market_segments.values())
        combined = '|'.join(patterns)
        return f'^({combined})$'


class AShareStockNameValidator:
    """
    Production-ready validator for Chinese A-share stock names
    """

    def __init__(self):
        self.pattern = re.compile(
            r'^'                    # Start of string
            r'(ST|\*ST|N)?'         # Optional prefix (ST, *ST, N)
            r'([\u4e00-\u9fff]+)'   # Chinese characters (required)
            r'([A-Z])?'             # Optional suffix (A, B, etc.)
            r'$'                    # End of string
        )

        # Common invalid patterns (for additional filtering)
        self.invalid_patterns = [
            r'.*[0-9].*',          # Contains numbers
            r'.*[-\s].*',          # Contains hyphens or spaces
            r'^[A-Z]+$',           # Only English letters
        ]

    def is_valid(self, name):
        """Check if name is valid A-share stock name"""
        if not isinstance(name, str) or len(name) < 2 or len(name) > 10:
            return False

        # Check against invalid patterns first
        for invalid_pattern in self.invalid_patterns:
            if re.match(invalid_pattern, name):
                return False

        # Check against valid pattern
        return bool(self.pattern.match(name))

    def get_name_components(self, name):
        """Extract components from valid stock name"""
        if not self.is_valid(name):
            return None

        match = self.pattern.match(name)
        if match:
            prefix, chinese, suffix = match.groups()
            return {
                'prefix': prefix,
                'chinese_name': chinese,
                'suffix': suffix
            }
        return None


if __name__ == '__init__':
    # Usage example
    validator = AShareStockNameValidator()

    stocks_to_check = [
        "平安银行", "万科A", "ST康美", "*ST飞马",
        "N京沪", "601318", "腾讯控股", "阿里巴巴-SW"
    ]

    print("Production Validation Results:")
    for stock in stocks_to_check:
        is_valid = validator.is_valid(stock)
        components = validator.get_name_components(stock) if is_valid else None
        status = f"VALID - {components}" if is_valid else "INVALID"
        print(f"'{stock}': {status}")

    # Usage example
    validator = AShareStockCodeValidator()
    # Test various codes
    codes_to_check = [
        "000001", "600036", "300001", "688001",
        "002415", "900901", "123456", "601318",
        "000858", "300750", "688981", "999999"
    ]
    print("Production Validation Results:")
    for code in codes_to_check:
        is_valid, info = validator.validate(code)
        status = "✅" if is_valid else "❌"
        print(f"{status} '{code}': {info}")
    # Batch validation
    print("\nBatch Validation:")
    batch_results = validator.validate_batch(codes_to_check)
    for code, result in batch_results.items():
        status = "✅" if result['valid'] else "❌"
        print(f"{status} '{code}': {result['info']}")
    # Get the comprehensive regex pattern
    print("\nComprehensive Regex Pattern:")
    print(validator.generate_regex_pattern())
