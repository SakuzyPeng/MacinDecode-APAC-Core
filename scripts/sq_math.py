"""High-precision mathematical primitives; no production tables or system libm.

Decimal values are converted directly to IEEE bits with integer arithmetic to
avoid double rounding through the host's binary64 when generating binary32.
"""
from decimal import Decimal, getcontext, localcontext
from functools import lru_cache
import struct

D = Decimal


@lru_cache(maxsize=8)
def pi(precision):
    with localcontext() as ctx:
        ctx.prec = precision + 12

        def atan_inverse(n):
            x = D(1) / n
            power, total, i = x, x, 1
            while True:
                power /= -n * n
                term = power / (2 * i + 1)
                updated = total + term
                if updated == total:
                    return total
                total, i = updated, i + 1

        value = 16 * atan_inverse(5) - 4 * atan_inverse(239)
        ctx.prec = precision
        return +value


def sin_pi(numerator, denominator):
    """sin(pi * rational), with exact quadrants and convergent Taylor series."""
    numerator %= 2 * denominator
    sign = 1
    if numerator > denominator:
        numerator -= denominator
        sign = -1
    if 2 * numerator > denominator:
        numerator = denominator - numerator
    if numerator == 0:
        return D(0)
    if 2 * numerator == denominator:
        return D(sign)
    precision = getcontext().prec
    with localcontext() as ctx:
        ctx.prec += 12
        x = pi(ctx.prec) * numerator / denominator
        term = total = x
        i = 1
        while True:
            term *= -x * x / ((2 * i) * (2 * i + 1))
            updated = total + term
            if updated == total:
                ctx.prec = precision
                return +(sign * total)
            total, i = updated, i + 1


def cos_pi(numerator, denominator):
    return sin_pi(2 * numerator + denominator, 2 * denominator)


def inverse_magnitude(q):
    """The positive real cube root of q**4, with guard digits."""
    if q == 0:
        return D(0)
    precision = getcontext().prec
    with localcontext() as ctx:
        ctx.prec += 12
        number = D(q**4)
        x = D(1 << ((int(number).bit_length() + 2) // 3))
        previous = None
        for _ in range(128):
            updated = (2 * x + number / (x * x)) / 3
            if (updated == x or updated == previous
                    or abs(updated - x) < D(1).scaleb(updated.adjusted() - precision - 6)):
                ctx.prec = precision
                return +updated
            previous, x = x, updated
        raise ArithmeticError('cube-root iteration did not converge')


def scale_gain(sf):
    whole, quarter = divmod(sf - 100, 4)
    return D(2) ** whole * D(2).sqrt().sqrt() ** quarter


def ieee_bits(value, width):
    """Round a finite Decimal directly to binary32/64, nearest, ties to even."""
    if not value.is_finite():
        raise ValueError('nonfinite mathematical value')
    if width not in (32, 64):
        raise ValueError('IEEE width must be 32 or 64')
    if value == 0:
        return 0
    p, emin, emax = (24, -126, 127) if width == 32 else (53, -1022, 1023)
    negative = value < 0
    numerator, denominator = value.copy_abs().as_integer_ratio()
    exponent = numerator.bit_length() - denominator.bit_length()
    below = numerator < denominator << exponent if exponent >= 0 else numerator << -exponent < denominator
    if below:
        exponent -= 1
    exponent = max(exponent, emin)
    shift = p - 1 - exponent
    if shift >= 0:
        numerator <<= shift
    else:
        denominator <<= -shift
    mantissa, remainder = divmod(numerator, denominator)
    if 2 * remainder > denominator or (2 * remainder == denominator and mantissa & 1):
        mantissa += 1
    if mantissa == 1 << p:
        exponent += 1
        mantissa >>= 1
    if exponent > emax:
        raise OverflowError('mathematical value exceeds IEEE range')
    if mantissa == 0:
        return 0  # Canonical zero is part of the numerical profile.
    if mantissa < 1 << (p - 1):
        word = mantissa
    else:
        word = ((exponent - emin + 1) << (p - 1)) | (mantissa - (1 << (p - 1)))
    return word | (int(negative) << (width - 1))


def from_bits(word, width):
    return struct.unpack('<f' if width == 32 else '<d', word.to_bytes(width // 8, 'little'))[0]


def round_f32(value):
    return from_bits(ieee_bits(value, 32), 32)
