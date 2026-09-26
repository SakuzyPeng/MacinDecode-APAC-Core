"""High-precision packet-state model driven only by generator truth."""
from decimal import Decimal
from sq_oracle import Channel
from sq_math import round_f32
from bwe2_oracle import expanded


class Decoder:
    def __init__(self):self.reset()
    def reset(self):self.channels=[Channel(strict_transitions=False),Channel(strict_transitions=False)]
    def decode(self,truth):
        if truth['embedded_preroll'] is not None:
            self.decode(truth['embedded_preroll']['truth'])
        if truth['absent']:
            sides=[[round_f32(v) for v in channel.overlap] for channel in self.channels]
            for channel in self.channels:channel.overlap=[Decimal(0)]*1024
        else:
            spectra=truth['spectra']
            sides=[channel.render(values,c['ics']['block_type']) for channel,values,c in
                   zip(self.channels,expanded(spectra),spectra['channels'])]
        return [v for pair in zip(*sides) for v in pair]
