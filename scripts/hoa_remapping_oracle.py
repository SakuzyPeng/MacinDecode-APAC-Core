"""Independent carrier permutation followed by existing Decimal recovery."""
from hoa_source_layout_oracle import Decoder as SourceDecoder
from hoa_remapping_vectors import effective


class Decoder(SourceDecoder):
    def __init__(self,opts):
        super().__init__(opts);self.permutation=effective(opts['remapping'])
    def core_sources(self,sources,truth,opts):
        n=len(self.permutation)
        return [sources[i] for i in self.permutation]+sources[n:]
