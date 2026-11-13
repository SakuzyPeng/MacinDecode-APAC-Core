"""Shared UniDRC field encoders with literal field truth."""
from spectrum_vectors import bits


def loudness(value=None, channels=2):
    if value is None:
        return bits(0, 20)
    return ('10' + bits(0, 16) + '10' + bits(1, 8) + bits(0, 8)
            + bits(channels, 8) + '01' + bits(value, 8) + '00')


def delta_time(value, ratio=16):
    if value == 1:
        return '00'
    if 2 <= value <= 5:
        return '01' + bits(value-2, 2)
    if 6 <= value <= 13:
        return '10' + bits(value-6, 3)
    return '11' + bits(value-14, (2*ratio-1).bit_length())
