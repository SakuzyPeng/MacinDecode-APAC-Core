"""Synthetic tests for the LSF continuation; no original BWE2 dictionaries."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
import numpy as np

from bwe2_blackbox_math import envelope
from bwe2_blackbox_wire import source,canonical,digest,bits,pack
from bwe2_lsf_refine import log_envelope_jacobian,fit_fixed_gain,relative_factorization,condition,condition_jacobian,joint_envelope_fit
from bwe2_lsf_probe import Experiment,read_bwe_syntax,CalibrationWriter


class Fitting(unittest.TestCase):
    def test_factored_derivative(self):
        f=np.arange(1,17)*12000/17+np.sin(np.arange(16))*25
        bins=np.arange(1,512,3)
        _,jac=log_envelope_jacobian(f,bins)
        for k in (0,7,15):
            left=f.copy();right=f.copy();left[k]-=1e-3;right[k]+=1e-3
            numeric=(log_envelope_jacobian(right,bins)[0]-log_envelope_jacobian(left,bins)[0])/.002
            self.assertLess(np.max(abs(numeric-jac[:,k])),1e-8)

    def test_fixed_gain_recovers_synthetic_frequencies(self):
        f=np.arange(1,17)*12000/17+np.sin(np.arange(16))*25
        carrier=np.array(source('comb',27),dtype=float)*128
        values=carrier.copy();bins=np.arange(512);gain=.371
        values[256:768]=carrier[128+bins%128]*gain/envelope(f)
        fitted=fit_fixed_gain(values,carrier,f+np.cos(np.arange(16))*2,gain,spacing=50.)
        self.assertLess(max(abs(np.array(fitted['lsf'])-f)),1e-5)
        self.assertLess(fitted['relative_transfer_rms'],1e-9)

    def test_relative_graph_uses_distinct_stage_indices(self):
        f=np.arange(1,17)*600.+100
        records=[dict(pair=[a,b],lsf=(f+a*11+b*3).tolist()) for a in (0,2) for b in (7,11,13)]
        result=relative_factorization(records)
        self.assertEqual(result['first_indices'],[0,2]);self.assertEqual(result['second_indices'],[7,11,13])
        self.assertTrue(all(r['identified'] for r in result['dimensions']))
        self.assertLess(max(r['max_frequency_error'] for r in result['predictions']),1e-8)
        self.assertFalse(result['original_tables_identified'])

    def test_disconnected_graph_remains_ambiguous(self):
        f=np.arange(1,17)*600.+100
        result=relative_factorization([dict(pair=[0,7],lsf=f.tolist()),dict(pair=[2,11],lsf=(f+20).tolist())])
        self.assertFalse(any(r['identified'] for r in result['dimensions']))

    def test_conditioning_jacobian_in_clipped_regions(self):
        x=np.arange(1,17)*700.;x[1]=x[0]+20;x[-1]=12100
        value,jac=condition_jacobian(x)
        self.assertTrue(np.array_equal(value,condition(x)))
        for k in (0,1,15):
            left=x.copy();right=x.copy();left[k]-=.001;right[k]+=.001
            actual=(condition(right)-condition(left))/.002
            self.assertLess(np.max(abs(actual-jac[:,k])),1e-8)

    def test_joint_envelope_on_synthetic_books(self):
        first=np.array([np.zeros(16),20+np.cos(np.arange(16))])
        second=np.array([np.arange(1,17)*600+100+j*3 for j in range(3)],dtype=float)
        observations=[];bins=np.arange(1,512,2)
        for a in range(2):
            for b in range(3):
                values,_=log_envelope_jacobian(first[a]+second[b],bins)
                observations.append(dict(pair=[a,b+10],log_envelope=values.tolist()))
        start=dict(first_indices=[0,1],second_indices=[10,11,12],first=(first+np.array([np.zeros(16),np.sin(np.arange(16))*.1])).tolist(),
                   second=(second+np.cos(np.arange(16))*.1).tolist())
        result=joint_envelope_fit(start,observations,1.)
        self.assertLess(result['log_rms'],1e-9)
        self.assertFalse(result['eligible_lsf'])

    def test_calibration_mixtures_reject_duplicate_lines(self):
        writer=CalibrationWriter()
        self.assertEqual(len(writer.program(dict(source='basis-mixture',coefficients=[[257,1],[511,-1]]))),2)
        with self.assertRaisesRegex(ValueError,'duplicate'):
            writer.program(dict(source='basis-mixture',coefficients=[[257,1],[257,-1]]))


class Syntax(unittest.TestCase):
    def fixture(self,root,flags,parameters,groups=None):
        groups=groups or [[1],[1]]
        wire='10101'+flags+parameters;raw=pack(wire)
        (root/'packets.bin').write_bytes(raw)
        (root/'packets.jsonl').write_bytes(canonical(dict(packet_index=0,export_offset=0,bytes=len(raw),sha256=digest(raw)))+b'\n')
        report=root/'tns.jsonl'
        report.write_bytes(canonical(dict(packet_index=0,status='partial',report=dict(tns_complete=True,
            stop_bit_offset=5,packet_sha256=digest(raw),channels=[dict(ics=dict(max_sfb=1,window_groups=g)) for g in groups])))+b'\n')
        return report

    def test_independent_literal_indices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);parameters=bits(37,9)+bits(451,9)+bits(16,6)+bits(0,9)+bits(511,9)+bits(63,6)
            report=self.fixture(root,'11',parameters)
            result=read_bwe_syntax(root,report)
            self.assertEqual(result['active_channel_frames'],2)
            self.assertEqual(result['unique_pairs'],[(0,511),(37,451)])

    def test_shared_parameters_and_bad_group_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);parameters=bits(23,9)+bits(51,9)+bits(7,6)
            result=read_bwe_syntax(root,self.fixture(root,'10',parameters))
            self.assertEqual(result['unique_pairs'],[(23,51)])
            with self.assertRaisesRegex(RuntimeError,'undefined reused'):
                read_bwe_syntax(root,self.fixture(root,'10',parameters,[[1],[1,1]]))

    def test_disabled_and_target_value_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);report=self.fixture(root,'00','')
            self.assertEqual(read_bwe_syntax(root,report)['active_channel_frames'],0)
            row=json.loads(report.read_bytes());row['report']['bwe2']={};report.write_bytes(canonical(row))
            with self.assertRaisesRegex(RuntimeError,'target-value output'):read_bwe_syntax(root,report)


class Scheduling(unittest.TestCase):
    def test_commands_reuse_outputs_and_share_native_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);exp=Experiment.__new__(Experiment)
            exp.out=root;exp.evidence=root/'evidence';exp.evidence.mkdir();exp.binary=Path('/synthetic/mapac')
            exp.identity={'fake':True};exp.check=lambda:None
            exp.config=dict(max_native_calls=2,max_evidence_bytes=32*1024**2,timeout_seconds=30)
            exp.db=sqlite3.connect(':memory:')
            exp.db.execute('CREATE TABLE commands(id INTEGER PRIMARY KEY,label TEXT,request BLOB,status TEXT,native_credits INTEGER,started REAL,result BLOB)')
            class Process:
                returncode=0
                def communicate(self,timeout):return b'public-result',b''
            with mock.patch('bwe2_lsf_probe.subprocess.Popen',return_value=Process()) as native:
                first=exp.command('fixture',['fixture'],credits=2)
                self.assertEqual(exp.command('fixture',['fixture'],credits=2),first)
                self.assertEqual(native.call_count,1)
                with self.assertRaisesRegex(RuntimeError,'budget exhausted'):exp.command('more',['different'],credits=1)
            exp.db.close()


if __name__=='__main__':unittest.main()
