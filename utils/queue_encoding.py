"""
Alphabetical encoding system for map request queue.
Converts between alphabetical codes (a, b, c, ..., aa, ab, ac, ...) and numbers.
"""

import string

def number_to_alpha(n: int) -> str:
    """
    Convert a number to alphabetical code (1-based).
    1 -> 'a', 2 -> 'b', ..., 26 -> 'z', 27 -> 'aa', 28 -> 'ab', etc.
    """
    if n <= 0:
        raise ValueError("Number must be positive")
    
    result = ""
    while n > 0:
        n -= 1  # Convert to 0-based
        remainder = n % 26
        result = chr(ord('a') + remainder) + result
        n //= 26
    
    return result

def alpha_to_number(alpha: str) -> int:
    """
    Convert alphabetical code to number.
    'a' -> 1, 'b' -> 2, ..., 'z' -> 26, 'aa' -> 27, 'ab' -> 28, etc.
    """
    alpha = alpha.lower().strip()
    if not alpha.isalpha():
        raise ValueError("Input must contain only letters")
    
    result = 0
    for char in alpha:
        result = result * 26 + (ord(char) - ord('a') + 1)
    
    return result

def get_next_alpha_code(current_codes: list[str]) -> str:
    """
    Get the next available alphabetical code.
    If no codes exist, returns 'a'.
    If codes exist, returns the next code after the highest one.
    """
    if not current_codes:
        return 'a'
    
    # Convert all codes to numbers, find max
    numbers = [alpha_to_number(code) for code in current_codes]
    max_num = max(numbers)
    
    # Return next code
    return number_to_alpha(max_num + 1)

def sort_alpha_codes(codes: list[str]) -> list[str]:
    """
    Sort alphabetical codes in numerical order.
    ['c', 'a', 'b', 'aa'] -> ['a', 'b', 'c', 'aa']
    """
    return sorted(codes, key=alpha_to_number)

def generate_alpha_range(start: int, end: int) -> list[str]:
    """Generate alphabetical codes from start to end (inclusive)."""
    return [number_to_alpha(i) for i in range(start, end + 1)]

# Test the functions
if __name__ == "__main__":
    # Test basic conversions
    test_cases = [
        (1, 'a'),
        (2, 'b'),
        (26, 'z'),
        (27, 'aa'),
        (28, 'ab'),
        (52, 'az'),
        (53, 'ba'),
        (702, 'zz'),
        (703, 'aaa'),
    ]
    
    print("Testing number_to_alpha:")
    for num, expected in test_cases:
        result = number_to_alpha(num)
        status = "[OK]" if result == expected else "[FAIL]"
        print(f"  {status} {num} -> {result} (expected: {expected})")
    
    print("\nTesting alpha_to_number:")
    for expected, alpha in test_cases:
        result = alpha_to_number(alpha)
        status = "[OK]" if result == expected else "[FAIL]"
        print(f"  {status} {alpha} -> {result} (expected: {expected})")
    
    print("\nTesting get_next_alpha_code:")
    test_sets = [
        ([], 'a'),
        (['a'], 'b'),
        (['a', 'b', 'c'], 'd'),
        (['z'], 'aa'),
        (['aa', 'ab'], 'ac'),
        (['az'], 'ba'),
    ]
    
    for codes, expected in test_sets:
        result = get_next_alpha_code(codes)
        status = "[OK]" if result == expected else "[FAIL]"
        print(f"  {status} {codes} -> {result} (expected: {expected})")
    
    print("\nTesting sort_alpha_codes:")
    test_sort = [['c', 'a', 'b', 'aa', 'z', 'ab']]
    for codes in test_sort:
        sorted_codes = sort_alpha_codes(codes)
        print(f"  {codes} -> {sorted_codes}")