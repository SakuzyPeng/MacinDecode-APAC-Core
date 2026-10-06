"""Public-syntax HOA mode-0 controls for pre-synthesis BWE2 cancellation."""
import math
import struct

from bwe2_blackbox_wire import Writer as SqWriter,bits,pack,bundle,canonical,digest


def escaped(value):
    if not 0<=value<78:raise ValueError('value exceeds bounded HOA escape')
    return bits(value,4) if value<15 else bits(15,4)+bits(value-15,6)


def hoa_cookie(order=1,components=4,method=0,subbands=None):
    n=(order+1)**2
    subbands=[16]*components if subbands is None else subbands
    if len(subbands)!=components or any(not 1<=v<=16 for v in subbands):raise ValueError('invalid HOA subband declarations')
    if method not in (0,2):raise ValueError('unsupported independent partition formula')
    fields=[(0,32),(int.from_bytes(b'dapa','big'),32),(0,32),(0x800,16),
            (5,6),(0 if n<=16 else 1,4),(0,1),(3,6),(0,6),(n,8),(2,8),(0,1),(1,3),(0,8),(2,3)]
    wire=''.join(bits(v,w) for v,w in fields)
    wire+='1100110'+bits(1,2)+bits(method,2)+bits(3,2)+bits(order,4)+escaped(components)+bits(0,(n-1).bit_length())
    wire+=''.join(escaped(count-1)+bits(order,order.bit_length()) for count in subbands)+'0'+bits(n,5)+'000'*n
    wire+='0'+bits(190,16)+bits(n,16)+'0'+'0'+bits(0,3)+bits(0,2)+'000000'
    raw=pack(wire)
    return len(raw).to_bytes(4,'big')+raw[4:]


def split_float(word):
    if type(word) is not int or not 0<=word<0x7f800000:raise ValueError('positive finite word required')
    if word==0:return [(100,0)]*3
    exponent=((word>>23)&255)-127
    if (word>>23)==0:raise ValueError('subnormals outside current cancellation range')
    mantissa=(word&0x7fffff)|(1<<23)
    values=[]
    for byte,shift in enumerate((16,8,0)):
        digit=(mantissa>>shift)&255
        amplitude_exponent=exponent+1-8*byte
        gain=100+4*amplitude_exponent
        if not 0<=gain<=255:raise ValueError('cancellation carrier gain out of range')
        values.append((gain,digit))
    return values


class NullWriter:
    def __init__(self):
        self.sq=SqWriter()
        # The cookie selects spatial method 0: interpolate the public Zwicker
        # band edges, then round once to the short-window grid.
        offsets=[0,100,200,300,400,510,630,770,920,1080,1270,1480,1720,2000,2320,2700,
                 3150,3700,4400,5300,6400,7700,9500,12000,15500,24000]
        self.ends=[]
        for i in range(1,17):
            position=25*i/16;index=math.floor(position)
            value=offsets[-1] if index==25 else offsets[index]+(position-index)*(offsets[index+1]-offsets[index])
            value*=1024/24000
            self.ends.append(math.floor(value/8+.5)*8)

    def signature(self):
        return dict(channels=4,layout_tag=(190<<16)|4,cookie_sha256=digest(hoa_cookie()))

    def audio(self,line,gain,parameters=None):
        values=[0]*1024;values[line]=1
        payload='10'+self.sq.channel(values,gain)[2:]+'0'
        if parameters is None:payload+='0'
        else:payload+='1'+bits(parameters[0],9)+bits(parameters[1],9)+bits(parameters[2],6)
        return payload

    def packet(self,spec):
        target=spec.get('target_line',256)
        source_line=spec.get('source_line',128)
        if not 256<=target<768 or not 128<=source_line<256 or (target-256)%128!=source_line-128:
            raise ValueError('target is not a lower-tier BWE2 copy of the source')
        band=next(i for i,end in enumerate(self.ends) if target<end)
        start=0 if band==0 else self.ends[band-1]
        if sum(start<=256+source_line-128+128*i<self.ends[band] for i in range(4))!=1 or start<=source_line<self.ends[band]:
            raise ValueError('selected band does not isolate the target spectral line')
        parts=split_float(spec.get('word',0));enabled=spec.get('bwe',True)
        audio=self.audio(source_line,spec.get('source_gain',160),spec.get('parameters',[0,0,63])) if enabled else '0'
        audio+=''.join(self.audio(target,gain) if digit else '0' for gain,digit in parts)
        coefficients=[]
        weights=[0 if enabled else 256]+[256+digit for _,digit in parts]
        for component in range(4):
            for b in range(16):
                coefficients.extend([weights[component] if b==band else 256,256,256,256])
        wire='0100'+audio+'1000'+''.join(bits(q,9) for q in coefficients)
        return pack(wire)+b'\0'

    def program(self,spec):
        silence=self.packet(dict(bwe=False,word=0))
        return [self.packet(spec),silence]

    def bundle(self,folder,packets):
        # The transport container is shared; only the explicit public format,
        # channel layout and cookie change from the two-channel packet bundle.
        bundle(folder,packets)
        cfg=hoa_cookie();(folder/'cookie.bin').write_bytes(cfg)
        import json
        manifest=json.loads((folder/'manifest.json').read_bytes())
        manifest['file']['format']['channels']=4
        manifest['file']['layout']['value'].update(tag=(190<<16)|4,ambisonic_order=1,
            ambisonic_channel_order='ACN',ambisonic_normalization='SN3D')
        manifest['file']['cookie']['value']=dict(bytes=len(cfg),sha256=digest(cfg))
        (folder/'manifest.json').write_bytes(canonical(manifest))


class DualNullWriter(NullWriter):
    """Opposite sign controls keep source power fixed and isolate BWE2 copies."""
    def signature(self):
        return dict(channels=9,layout_tag=(190<<16)|9,cookie_sha256=digest(hoa_cookie(2,8)))

    def targets(self,spec):
        source_line=spec.get('source_line',129);target=spec.get('target_line',source_line+128)
        if source_line%2!=1 or not 129<=source_line<256 or (target-source_line-128)%128!=0 or not 256<=target<768:
            raise ValueError('invalid odd-comb target')
        band=next(i for i,end in enumerate(self.ends) if target<end)
        start=0 if band==0 else self.ends[band-1]
        targets=[source_line+128+128*i for i in range(4) if start<=source_line+128+128*i<self.ends[band]]
        if not 1<=len(targets)<=2:raise ValueError('more than two cancellation targets')
        return band,targets

    def packet(self,spec):
        band,targets=self.targets(spec)
        words=spec.get('words',[0]*len(targets))
        if len(words)!=len(targets):raise ValueError('wrong candidate word count')
        enabled=spec.get('bwe',True);audio='';weights=[]
        if enabled:
            base=[int(k%2==1 and k<256) for k in range(1024)]
            if spec.get('seed') is not None:
                state=spec['seed']&0xffffffff
                for k in range(1,256,2):
                    state=(1664525*state+1013904223)&0xffffffff
                    base[k]=1 if state&0x80000000 else -1
                base[spec.get('source_line',129)]=1
            for channel in range(2):
                values=base.copy()
                if channel and spec.get('flip',True):values[spec.get('source_line',129)]=-1
                parameters=spec.get('parameters',[0,0,63])
                audio+='10'+self.sq.channel(values,spec.get('source_gain',128))[2:]+'01'
                audio+=bits(parameters[0],9)+bits(parameters[1],9)+bits(parameters[2],6)
            weights=[128,384]
        else:audio='00';weights=[256,256]
        for line,word in zip(targets,words):
            for gain,digit in split_float(word):
                audio+=self.audio(line,gain) if digit else '0';weights.append(256+digit)
        while len(weights)<8:audio+='0';weights.append(256)
        audio+='0'  # ninth transport is not a salient component
        coefficients=[]
        for weight in weights:
            for b in range(16):coefficients.extend([weight if b==band else 256]+[256]*8)
        return pack('0100'+audio+'1000'+''.join(bits(q,9) for q in coefficients))+b'\0'

    def program(self,spec):
        silence=self.packet(dict(bwe=False,words=[0]))
        if 'sequence' in spec:
            if not 1<=len(spec['sequence'])<=64:raise ValueError('sequence outside bounded nulling program')
            return [packet for item in spec['sequence'] for packet in (self.packet(item),silence)]
        return [self.packet(spec),silence]

    def bundle(self,folder,packets):
        bundle(folder,packets)
        cfg=hoa_cookie(2,8);(folder/'cookie.bin').write_bytes(cfg)
        import json
        manifest=json.loads((folder/'manifest.json').read_bytes())
        manifest['file']['format']['channels']=9
        manifest['file']['layout']['value'].update(tag=(190<<16)|9,ambisonic_order=2,
            ambisonic_channel_order='ACN',ambisonic_normalization='SN3D')
        manifest['file']['cookie']['value']=dict(bytes=len(cfg),sha256=digest(cfg))
        (folder/'manifest.json').write_bytes(canonical(manifest))


class ParallelNullWriter(DualNullWriter):
    """Twenty-five candidate channels observe the same BWE2 computation."""
    def signature(self):
        return dict(channels=25,layout_tag=(190<<16)|25,cookie_sha256=digest(hoa_cookie(4,8)))

    def program(self,spec):
        if 'sequence' not in spec:return super().program(spec)
        if not 1<=len(spec['sequence'])<=256:raise ValueError('coherent nulling program exceeds 256 query frames')
        silence=self.packet(dict(bwe=False,words=[0]))
        return [packet for item in spec['sequence'] for packet in (self.packet(item),silence)]

    def packet(self,spec):
        band,targets=self.targets(spec)
        candidates=spec.get('candidates',[spec.get('words',[0]*len(targets))]*25)
        if len(candidates)!=25 or any(len(row)!=len(targets) for row in candidates):raise ValueError('candidate grid dimensions differ')
        parts=[[split_float(w) for w in row] for row in candidates]
        audio='';weights=[]
        if spec.get('bwe',True):
            base=[int(k%2==1 and k<256) for k in range(1024)]
            if spec.get('seed') is not None:
                state=spec['seed']&0xffffffff
                for k in range(1,256,2):
                    state=(1664525*state+1013904223)&0xffffffff;base[k]=1 if state&0x80000000 else -1
                base[spec.get('source_line',129)]=1
            for channel in range(2):
                values=base.copy()
                if channel and spec.get('flip',True):values[spec.get('source_line',129)]=-1
                parameters=spec.get('parameters',[0,0,63])
                audio+='10'+self.sq.channel(values,spec.get('source_gain',128))[2:]+'01'
                audio+=bits(parameters[0],9)+bits(parameters[1],9)+bits(parameters[2],6)
            weights=[[128]*25,[384]*25]
        else:audio='00';weights=[[256]*25,[256]*25]
        for j,line in enumerate(targets):
            for byte in range(3):
                active=[parts[ch][j][byte] for ch in range(25) if parts[ch][j][byte][1]]
                gains={gain for gain,_ in active}
                if len(gains)>1:raise ValueError('candidate grid crosses an exponent boundary')
                if gains:audio+=self.audio(line,next(iter(gains)))
                else:audio+='0'
                weights.append([256+parts[ch][j][byte][1] for ch in range(25)])
        while len(weights)<8:audio+='0';weights.append([256]*25)
        audio+='0'*17
        coefficients=[]
        for vector in weights:
            for b in range(16):coefficients.extend(vector if b==band else [256]*25)
        return pack('0100'+audio+'1000'+''.join(bits(q,9) for q in coefficients))+b'\0'

    def bundle(self,folder,packets):
        bundle(folder,packets)
        cfg=hoa_cookie(4,8);(folder/'cookie.bin').write_bytes(cfg)
        import json
        manifest=json.loads((folder/'manifest.json').read_bytes())
        manifest['file']['format']['channels']=25
        manifest['file']['layout']['value'].update(tag=(190<<16)|25,ambisonic_order=4,
            ambisonic_channel_order='ACN',ambisonic_normalization='SN3D')
        manifest['file']['cookie']['value']=dict(bytes=len(cfg),sha256=digest(cfg))
        (folder/'manifest.json').write_bytes(canonical(manifest))


class SingleCarrierWriter(NullWriter):
    """One BWE carrier; a period-16 power comb and four tones per linear band.

    Three source slots also permit a fully known 24-bit synthetic control.
    Twelve cancellation slots represent four independent Float32 candidates.
    All partition boundaries are the public equal-width formula (method 2).
    """
    order,channels,components,prefix_slots=3,16,15,3
    phases=(8,)

    def __init__(self):
        self.sq=SqWriter();self.ends=list(range(64,1025,64))

    def signature(self):
        return dict(channels=self.channels,layout_tag=(190<<16)|self.channels,
                    cookie_sha256=digest(hoa_cookie(self.order,self.components,2)))

    def targets(self,spec):
        band=spec.get('band',4)
        if type(band) is not int or not 4<=band<12:raise ValueError('single-carrier band outside extension')
        phase=spec.get('phase',8)
        if phase not in self.phases:raise ValueError('unsupported symmetric sparse-comb phase')
        return band,[k for k in range(64*band,64*(band+1)) if k%16 in (phase,16-phase)]

    def packet(self,spec):
        band,targets=self.targets(spec)
        candidates=spec.get('candidates',[spec.get('words',[0]*len(targets))]*self.channels)
        if len(candidates)!=self.channels or any(len(row)!=len(targets) for row in candidates):raise ValueError('single-carrier grid dimensions differ')
        parts=[[split_float(w) for w in row] for row in candidates];audio='';weights=[];phase=spec.get('phase',8)
        if 'synthetic_word' in spec:
            coordinate=spec.get('coordinate',0)
            if coordinate not in range(len(targets)):raise ValueError('synthetic coordinate outside band')
            hidden=split_float(spec['synthetic_word'])
            if self.prefix_slots==1 and any(digit for _,digit in hidden[1:]):raise ValueError('wide control requires an eight-bit mantissa')
            for gain,digit in hidden[:self.prefix_slots]:
                audio+=self.audio(targets[coordinate],gain) if digit else '0'
                weights.append([256-digit]*self.channels)
        elif spec.get('bwe',True):
            source=[int(k%16 in (phase,16-phase) and k<256) for k in range(1024)]
            seed=spec.get('seed')
            if seed is not None:
                state=seed&0xffffffff
                for k in range(128):
                    if source[k]:
                        state=(1664525*state+1013904223)&0xffffffff
                        source[k]=1 if state&0x80000000 else -1
            parameters=spec.get('parameters',[0,0,63])
            audio='10'+self.sq.channel(source,spec.get('source_gain',128))[2:]+'01'
            audio+=''.join(bits(v,width) for v,width in zip(parameters,(9,9,6)))+'0'*(self.prefix_slots-1)
            weights=[[0]*self.channels]+[[256]*self.channels for _ in range(self.prefix_slots-1)]
        else:audio='0'*self.prefix_slots;weights=[[256]*self.channels for _ in range(self.prefix_slots)]
        for j,line in enumerate(targets):
            for byte in range(3):
                active=[parts[ch][j][byte] for ch in range(self.channels) if parts[ch][j][byte][1]]
                gains={gain for gain,_ in active}
                if len(gains)>1:raise ValueError('candidate grid crosses an exponent boundary')
                audio+=self.audio(line,next(iter(gains))) if gains else '0'
                weights.append([256+parts[ch][j][byte][1] for ch in range(self.channels)])
        while len(weights)<self.components:audio+='0';weights.append([256]*self.channels)
        audio+='0'*(self.channels-self.components)
        coefficients=[]
        for vector in weights:
            for b in range(16):coefficients.extend(vector if b==band else [256]*self.channels)
        return pack('0100'+audio+'1000'+''.join(bits(q,9) for q in coefficients))+b'\0'

    def program(self,spec):
        sequence=spec.get('sequence',[spec])
        if not 1<=len(sequence)<=128:raise ValueError('single-carrier program exceeds 128 query frames')
        silence=self.packet(dict(bwe=False))
        return [p for item in sequence for p in (self.packet(item),silence)]

    def bundle(self,folder,packets):
        bundle(folder,packets);cfg=hoa_cookie(self.order,self.components,2);(folder/'cookie.bin').write_bytes(cfg)
        import json
        manifest=json.loads((folder/'manifest.json').read_bytes())
        manifest['file']['format']['channels']=self.channels
        manifest['file']['layout']['value'].update(tag=(190<<16)|self.channels,ambisonic_order=self.order,
            ambisonic_channel_order='ACN',ambisonic_normalization='SN3D')
        manifest['file']['cookie']['value']=dict(bytes=len(cfg),sha256=digest(cfg))
        (folder/'manifest.json').write_bytes(canonical(manifest))


class WideSingleCarrierWriter(SingleCarrierWriter):
    """Eight tones per band, using both +/- residues of a power comb."""
    order,channels,components,prefix_slots=4,25,25,1
    phases=(1,3,5,7,8)
