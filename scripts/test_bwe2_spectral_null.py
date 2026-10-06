"""Synthetic controls for spectral cancellation; no original BWE2 dictionaries."""
import json
from pathlib import Path
import platform
import unittest
import contextlib
import io
from unittest import mock

import numpy as np
from bwe2_blackbox_math import basis,fit_transfer,lsf_to_lpc
from bwe2_blackbox_wire import digest
from bwe2_float_profile import PublicDft,PublicRadix2
from bwe2_lpc_lattice import nearest_lattice,reduce_basis
from bwe2_lpc_polynomial import half_polynomials,full_polynomials,assemble
from bwe2_parallel_spectrum import candidate_grid
from bwe2_single_spectrum import grid
from bwe2_spectral_null_wire import escaped,hoa_cookie,split_float,SingleCarrierWriter,WideSingleCarrierWriter,ParallelNullWriter
from bwe2_spectral_residual import infer,calibrate
import test_bwe2_blackbox as baseline_tests
from test_bwe2_blackbox import FakeProcess
import bwe2_blackbox_capture as capture


class WireAndMathematics(unittest.TestCase):
    def test_three_dyadic_chunks_reproduce_float_words(self):
        for value in (1.,.125,128.,164.73929,47039.00390625,65535.99609375):
            word=np.float32(value).view(np.uint32).item()
            total=sum(2.**((gain-100)/4)*digit/256 for gain,digit in split_float(word))
            self.assertEqual(np.float32(total).view(np.uint32).item(),word)
        for word in (-1,1,0x7f800000,0x80000000):
            with self.assertRaises(ValueError):split_float(word)
        with self.assertRaises(ValueError):split_float(np.float32(.000012345).view(np.uint32).item())

    def test_cookie_escape_and_existing_signature(self):
        self.assertEqual(escaped(14),'1110');self.assertEqual(escaped(15),'1111000000')
        self.assertEqual(escaped(16),'1111000001')
        with self.assertRaises(ValueError):escaped(78)
        self.assertEqual(digest(hoa_cookie(3,15,2)),SingleCarrierWriter().signature()['cookie_sha256'])
        with self.assertRaises(ValueError):hoa_cookie(3,15,2,[17]*15)

    def test_sparse_power_comb_is_white_through_order_16(self):
        for phase in (1,3,5,7,8):
            power=np.array([k%16 in (phase,16-phase) for k in range(256)],float)
            self.assertTrue(np.all(np.fft.ifft(np.r_[power,0,power[:0:-1]]).real[1:17]==0))
        writer=SingleCarrierWriter();targets=[line for band in range(4,12) for line in writer.targets(dict(band=band))[1]]
        self.assertEqual(targets,list(range(264,768,16)))
        with self.assertRaises(ValueError):writer.program(dict(sequence=[dict(bwe=False)]*129))
        self.assertEqual(len(ParallelNullWriter().program(dict(sequence=[dict(bwe=False,words=[0])]*2))),4)
        wide=WideSingleCarrierWriter()
        coverage=[line-256 for phase in (1,3,5,7) for band in range(4,12) for line in wide.targets(dict(phase=phase,band=band))[1]]
        self.assertEqual(sorted(coverage),list(range(1,512,2)))
        self.assertEqual(wide.signature()['channels'],25)
        # Copy-region signs stay positive for cancellation, while the lower
        # spectrum may change its signs without changing the power controls.
        self.assertNotEqual(wide.packet(dict(phase=1,seed=20261007)),wide.packet(dict(phase=1)))

    def test_candidate_grid_keeps_exponent_boundaries(self):
        for word in (0x47000000,0x477fffff,0x473fffff):
            for values in candidate_grid([word]):self.assertEqual(values[0]>>23,word>>23)
            candidates=grid([word]*4)
            self.assertEqual(len({tuple(row) for row in candidates}),16)
            self.assertTrue(all(all(v>>23==word>>23 for v in row) for row in candidates))
        with self.assertRaises(ValueError):candidate_grid([0])

    def test_residual_lattice_recovers_simultaneous_known_words(self):
        targets=[264,280,296,312];true=np.float32([12345.125,34001.0625,17293.75,60101.5])
        centers=true.view(np.uint32).astype(np.int64)+[-17,23,-11,8]
        candidates=np.array(grid(centers.tolist()),np.uint32)
        ideal=basis()[:,targets]*2**-25
        raw=np.float32(ideal@(candidates.view(np.float32).astype(float)-true.astype(float)).T)
        result=infer(raw,candidates,targets,2**-25)
        self.assertTrue(result['qualified']);self.assertEqual(result['words'],true.view(np.uint32).tolist())
        # A non-basis component and a channel observing a different value must
        # not be mislabeled as one common exactly observed spectral vector.
        corrupt=raw.astype(float);corrupt[:,0]+=basis()[:,500]*1e-9
        self.assertFalse(infer(corrupt,candidates,targets,2**-25)['qualified'])
        corrupt=raw.astype(float);corrupt[:,0]+=ideal[:,0]*float(np.spacing(true[0]))
        self.assertFalse(infer(corrupt,candidates,targets,2**-25)['consistent_channels'])

    def test_zero_crosscheck_and_calibration(self):
        line=264;word=np.float32(47039.00390625).view(np.uint32).item()
        candidates=np.array([[word+i] for i in range(-8,8)],np.uint32)
        true=np.array([word],np.uint32).view(np.float32)
        raw=np.float32(basis()[:,[line]]*2**-25@(candidates.view(np.float32)-true).T)
        result=infer(raw,candidates,[line],2**-25)
        self.assertEqual(result['exact_zero_channels'],[8]);self.assertEqual(result['exact_zero_words'],[word])
        units=np.repeat(np.float32(basis()[:,[line]]*2**-25)[None,:,:],16,axis=2)
        report=calibrate(units,[line]);self.assertLess(abs(report['scale']/2**-25-1),1e-7)

    def test_sparse_transfer_factorization(self):
        a=lsf_to_lpc(np.arange(1,17)*12000/17+np.sin(np.arange(16))*15)
        bins=np.arange(8,512,16);z=np.exp(-1j*np.pi/512*bins[:,None]*np.arange(17)[None,:])@a
        fitted=fit_transfer(bins,1.25/abs(z))
        self.assertLess(max(abs(np.array(fitted['lpc'])-a)),1e-10)
        self.assertAlmostEqual(fitted['gain'],1.25,places=10)

    def test_bounded_lattice_search_and_singular_rejection(self):
        basis_=np.array([[1.,32.],[0.,1.],[.5,.25]])
        reduced,transform=reduce_basis(basis_)
        np.testing.assert_allclose(reduced,basis_@transform,rtol=0,atol=1e-12)
        target=basis_@np.array([7,-3])+[.01,-.01,0]
        choices,nodes=nearest_lattice(basis_,target,count=8)
        self.assertEqual(choices[0]['offset'],[7,-3]);self.assertGreater(nodes,0)
        with self.assertRaisesRegex(ValueError,'singular'):reduce_basis(np.ones((4,2)))
        with self.assertRaisesRegex(RuntimeError,'node bound'):nearest_lattice(basis_,target,count=8,node_limit=1)

    def test_float_polynomial_hypotheses_against_synthetic_polynomial(self):
        frequencies=np.arange(1,17)*12000/17+np.cos(np.arange(16))*20
        cosines=np.float32(np.cos(np.pi*frequencies/12000));expected=lsf_to_lpc(frequencies)
        for arithmetic in ('separate-sum','separate-product','fused-sum','fused-product','tree-separate-forward','tree-fused-reverse'):
            zero=half_polynomials(np.zeros((1,8),np.float32),list(range(8)),arithmetic)
            np.testing.assert_array_equal(zero[0],[1,0,8,0,28,0,56,0,70])
            halves=[half_polynomials(cosines[parity::2][None,:],list(range(8)),arithmetic) for parity in (0,1)]
            absolute=np.array([1.])
            for c in cosines[::2]:absolute=np.convolve(absolute,[1,2*abs(float(c)),1])
            bound=32*np.finfo(np.float32).eps*np.max(absolute)
            for final in ('grouped','left','symmetric'):
                self.assertLess(np.max(abs(assemble(*halves,final)[0]-expected)),bound)

    def test_full_quadratic_product_retains_asymmetric_rounding(self):
        cosines=np.float32(np.random.default_rng(519).uniform(-.95,.95,(3,8)))
        full=full_polynomials(cosines,list(range(8)),'separate-product')
        self.assertFalse(np.array_equal(full,full[:,::-1]))
        for row,actual in zip(cosines,full):
            expected=np.array([1.]);absolute=np.array([1.])
            for c in row:
                expected=np.convolve(expected,[1.,-2*float(c),1.]);absolute=np.convolve(absolute,[1.,2*abs(float(c)),1.])
            self.assertLess(max(abs(actual-expected)),32*np.finfo(np.float32).eps*max(absolute))
            self.assertEqual(actual[0],1);self.assertEqual(actual[-1],1)

    def test_encoder_sources_are_bounded_and_reproducible(self):
        from bwe2_public_encoder import synthesize
        from bwe2_encoder_pilot import cases
        self.assertEqual(len(cases()),16)
        for signal in ('vowel','noise','formant-noise','bwe-shaped-noise'):
            spec=dict(signal=signal,rate=24000,seconds=.25,channels=2,seed=52)
            first=synthesize(spec);second=synthesize(spec)
            self.assertEqual(first.shape,(6000,2));self.assertEqual(first.dtype,np.dtype('<f4'))
            self.assertTrue(np.array_equal(first,second));self.assertTrue(np.all(np.isfinite(first)))
            self.assertLessEqual(max(abs(first.ravel())),.700001)
        with self.assertRaises(ValueError):synthesize(dict(seconds=100))

    @unittest.skipUnless(platform.system()=='Darwin','public Accelerate API requires macOS')
    def test_public_fft_normalization_alignment_and_layout(self):
        a=np.r_[1.,np.sin(np.arange(16))*.1].astype(np.float32);expected=np.fft.fft(a,1024)[:512]
        for cls in (PublicDft,PublicRadix2):
            for real in (False,True):
                for in_place in (False,True):
                    transform=cls(real=real,in_place=in_place,offset=(0,4,8,12))
                    try:
                        re,im=transform.execute(a)
                        self.assertLess(np.max(abs(re+1j*im-expected)),5e-7)
                    finally:transform.close()

    @unittest.skipUnless(platform.system()=='Darwin','public Accelerate API requires macOS')
    def test_public_vector_magnitude_and_synthetic_model_fit(self):
        from bwe2_public_math import PublicMath
        from bwe2_public_lpc import fit,gain_value
        math=PublicMath();rng=np.random.default_rng(72)
        re=np.float32(rng.normal(size=512));im=np.float32(rng.normal(size=512))
        magnitude=math.magnitude(re,im,'zvabs');expected=np.hypot(re.astype(float),im.astype(float))
        self.assertLess(max(abs(magnitude/expected-1)),3e-7)
        np.testing.assert_allclose(math.divide(np.array([1.,2.,4.],np.float32),.5,'svdiv'),[64.,32.,16.],rtol=0,atol=0)
        a=np.float32(np.r_[1.,np.rint(np.sin(np.arange(16))*.002*2**20)/2**20]);fft=PublicDft(True,4)
        try:words=math.predict(a,gain_value(),fft.execute)[1:512:2].view(np.uint32)
        finally:fft.close()
        document=dict(words={str(k):int(w) for k,w in zip(range(1,512,2),words)},pair=[3,5],complete=True)
        with contextlib.redirect_stdout(io.StringIO()):result=fit(document)
        self.assertTrue(result['fit_complete']);self.assertFalse(result['eligible_lsf'])
        self.assertEqual(result['lpc_f32'],a.view(np.uint32).tolist())


class GeometryProcess(FakeProcess):
    def __init__(self,command,**kwargs):
        super().__init__(command,**kwargs)
        root=Path(command[command.index('--out')+1]);request=json.loads((root.parent/'request.json').read_bytes())
        signature=SingleCarrierWriter().signature();frames=request['frames']
        raw=np.zeros(frames*signature['channels'],dtype='<f4').tobytes()
        pcm=json.loads((root/'pcm.json').read_bytes());pcm.update(channels=signature['channels'],sha256=digest(raw),
            layout=dict(value=dict(tag=signature['layout_tag'])),source_cookie_sha256=signature['cookie_sha256'])
        (root/'pcm.json').write_text(json.dumps(pcm));(root/'pcm.f32le').write_bytes(raw)
        self.command=command


class CaptureGeometry(baseline_tests.Evidence):
    # The inherited fake-interface tests also exercise old stereo behavior.
    def test_legacy_manifest_and_key_remain_compatible(self):
        first=self.cap.probe('legacy',dict(source='silence'))
        self.cap.config.pop('wire_signature');(self.out/'manifest.json').write_text(json.dumps(self.cap.config))
        self.cap.close();self.cap=capture.Capture(self.out,self.root/'binary')
        self.assertEqual(first,self.cap.probe('legacy-resume',dict(source='silence')))
        self.assertEqual(self.cap.status()['attempts'],dict(success=1))

    def test_geometry_cache_identity_and_long_program_reservation(self):
        self.cap.close()
        # Capture initialization records git identity via real Popen.
        self.patches[3].stop()
        self.cap=capture.Capture(self.root/'hoa',self.root/'binary',self.mount/'hoa',self.mount,writer=SingleCarrierWriter())
        self.patches[3].start()
        with mock.patch.object(capture.subprocess,'Popen',side_effect=GeometryProcess) as popen:
            spec=dict(sequence=[dict(bwe=False)]*32)
            self.cap.probe('long',spec);self.cap.probe('cached',spec)
            self.assertEqual(popen.call_count,1)
            command=popen.call_args.args[0]
            self.assertGreater(int(command[command.index('--max-output-mib')+1]),4)
            self.assertGreater(self.cap.status()['evidence_uncompressed_bytes'],4*capture.MIB)
        self.cap.close()
        with self.assertRaisesRegex(RuntimeError,'wire signature'):
            capture.Capture(self.root/'hoa',self.root/'binary')
        self.cap=capture.Capture(self.root/'hoa',self.root/'binary',writer=SingleCarrierWriter())


if __name__=='__main__':unittest.main()
