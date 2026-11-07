"""Credit calculations using the same per-million-token prices as AI statistics."""

from math import isfinite


def calculate_credit_used(usage, price):
    input_tokens = usage.get('input_tokens') or 0
    cached_tokens = usage.get('cached_tokens') or 0
    output_tokens = usage.get('output_tokens') or 0
    input_tokens = input_tokens if isinstance(input_tokens, int) else 0
    cached_tokens = cached_tokens if isinstance(cached_tokens, int) else 0
    output_tokens = output_tokens if isinstance(output_tokens, int) else 0
    cached_tokens = min(max(cached_tokens, 0), max(input_tokens, 0))
    if not input_tokens and not output_tokens:
        return 0

    if isinstance(price, (int, float)):
        price = {'input_tokens': price, 'cached_tokens': price, 'output_tokens': 0}
    if not isinstance(price, dict) or 'input_tokens' not in price:
        return None

    input_price = price['input_tokens']
    cached_price = price.get('cached_tokens', input_price)
    output_price = price.get('output_tokens', 0)
    if any(not isinstance(value, (int, float)) or not isfinite(value) or value < 0
           for value in (input_price, cached_price, output_price)):
        return None
    return (
        input_price * (input_tokens - cached_tokens)
        + cached_price * cached_tokens
        + output_price * output_tokens
    ) / 1e6 * 100


def sum_credit_used(*usages):
    total = 0
    for usage in usages:
        credit_used = usage.get('credit_used')
        if credit_used is None:
            if any(usage.get(key) for key in ('input_tokens', 'output_tokens', 'total_tokens')):
                return None
        else:
            total += credit_used
    return total
