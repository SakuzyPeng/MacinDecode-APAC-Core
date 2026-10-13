"""Fault injection for the actual six-build acceptance gate."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from check_ci_consistency import check, upstream
from ci_catalog import CELLS, REFERENCE, digest, group, load, shards
from ci_evidence import SCHEMA, clean, coverage
import ci_support
from run_portable_suite import SUITE


class GateTests(unittest.TestCase):
    def setUp(self):
        self.rows = [dict(index=i, input_sha256=str(i)*64, pcm_sha256='a'*64, passed=True) for i in (1, 2)]
        entry = dict(groups={'cases': group(self.rows, ['index', 'input_sha256'])}, rejections=[])
        self.catalog = dict(profiles={'ci-fast': {'validator': entry}})
        self.documents = []
        for target, profile in CELLS:
            payload = dict(passed=True, cases=copy.deepcopy(self.rows), errors=[], numeric_profile='sq-v2',
                           ci_profile='ci-fast', code_commit='commit', source_sha256='source')
            self.documents.append(dict(schema=SCHEMA, passed=True, profile='ci-fast', validator='validator',
                mode='math' if (target, profile) == REFERENCE else 'collect',
                catalog_sha256=digest(self.catalog), entry_sha256=digest(entry), payload=payload,
                payload_sha256=digest(payload), build=dict(target=target, build_profile=profile,
                    binary_sha256='b'*64, code_commit='commit', source_sha256='source',
                    compiler='rustc 1.98.0 (test) | host: '+target, debug_assertions=profile=='debug')))

    def gate(self, documents=None):
        return check(self.documents if documents is None else documents, ['ci-fast'], 'commit', 'source', self.catalog)

    def damage(self, fn):
        documents = copy.deepcopy(self.documents)
        fn(documents[0])
        # Recompute the integrity hash: these tests exercise semantic validation,
        # rather than just detecting a corrupt JSON file.
        documents[0]['payload_sha256'] = digest(documents[0]['payload'])
        with self.assertRaises((AssertionError, ValueError, KeyError)):
            self.gate(documents)

    def test_complete_six_builds_pass(self):
        self.assertEqual(self.gate()['builds'], 6)

    def test_changed_pcm_bit_numeric_profile_commit_source_and_compiler_fail(self):
        self.damage(lambda d: d['payload']['cases'][0].update(pcm_sha256='b'+'a'*63))
        self.damage(lambda d: d['payload'].update(numeric_profile='sq-v1'))
        for key in ('code_commit', 'source_sha256', 'compiler'):
            self.damage(lambda d, key=key: d['build'].update({key:'wrong'}))
        self.damage(lambda d: d['payload'].update(code_commit='wrong'))
        self.damage(lambda d: d['build'].update(debug_assertions=False))

    def test_missing_duplicate_or_failed_cases_fail(self):
        self.damage(lambda d: d['payload']['cases'].pop())
        self.damage(lambda d: d['payload']['cases'].append(d['payload']['cases'][0]))
        self.damage(lambda d: d['payload']['cases'][0].update(passed=False))
        self.damage(lambda d: d['payload'].update(errors=['decoder failed']))
        self.damage(lambda d: d.update(passed=False))

    def test_missing_or_duplicate_platform_profile_fails(self):
        for documents in (self.documents[:-1], self.documents+[self.documents[0]], []):
            with self.assertRaises(AssertionError):self.gate(documents)

    def test_collection_cannot_qualify_the_reference(self):
        documents = copy.deepcopy(self.documents)
        next(d for d in documents if d['mode']=='math')['mode']='collect'
        with self.assertRaises(AssertionError):self.gate(documents)
        for reference in ({'mode':'fingerprint_collection','passed':True}, {'ci_profile':'ci-fast','passed':True}):
            with self.assertRaises(ValueError):ci_support.checked_reference(reference)

    def test_fast_cannot_impersonate_full(self):
        self.damage(lambda d: d.update(profile='ci-full'))
        self.damage(lambda d: d['payload'].update(ci_profile='ci-full'))
        self.damage(lambda d: d.update(entry_sha256='full-catalog'))

    def test_unknown_fields_and_paths_are_not_silently_discarded(self):
        self.damage(lambda d: d['payload'].update(new_numeric_path='/changed/semantic/value'))
        self.assertEqual(clean({'elapsed_new':123, 'new_path':'/input', 'compiler':'env'}),
                         {'elapsed_new':123, 'new_path':'/input'})

    def test_math_failures_cannot_be_hidden_by_normalization(self):
        entry=self.catalog['profiles']['ci-fast']['validator']
        for metric in ({'passed':False}, {'failed_samples':1}, {'first_failure':{'sample':0}}, {'error':float('nan')}):
            with self.assertRaises(AssertionError):coverage(dict(passed=True,cases=self.rows,metrics=metric),entry)

    def test_rejection_contract_requires_rejection_and_expected_success(self):
        entry=dict(groups={'cases':group(self.rows,['index'])},rejections=[2])
        report=dict(passed=True,cases=copy.deepcopy(self.rows))
        with self.assertRaises(AssertionError):coverage(report,entry)
        report['cases'][1]['expected']='rejection';coverage(report,entry)
        report['cases'][0]['expected']='rejection'
        with self.assertRaises(AssertionError):coverage(report,entry)

    def test_upstream_failure_cancellation_and_unexpected_skip_block_release(self):
        for full in (False, True):
            good={n:{'result':('success' if n=='build' or full else 'skipped')} for n in ('build','full_math','full_collect')}
            upstream(good,full)
            for name in good:
                for status in ('failure','cancelled','skipped' if good[name]['result']=='success' else 'success'):
                    bad=copy.deepcopy(good);bad[name]['result']=status
                    with self.assertRaises(AssertionError):upstream(bad,full)


class CatalogAndWorkflowTests(unittest.TestCase):
    def test_full_catalog_and_nine_shards_cover_every_validator_once(self):
        catalog=load()
        self.assertEqual(set(catalog['profiles']['ci-full']),set(SUITE))
        self.assertEqual(catalog['shards'],shards(SUITE))
        self.assertEqual(len(catalog['shards']),9)
        self.assertTrue(set(catalog['profiles']['ci-fast']) < set(SUITE))

    def test_collector_disables_oracle_without_becoming_a_reference(self):
        with patch.object(ci_support,'CONTEXT',{'mode':'collect'}):
            self.assertFalse(ci_support.mathematics())
        with patch.object(ci_support,'CONTEXT',{'mode':'math'}):
            self.assertTrue(ci_support.mathematics())
        self.assertFalse(ci_support.mathematics({'passed':True}))

    def test_expected_rejection_does_not_accept_crashes_or_success(self):
        with patch.object(ci_support,'CONTEXT',{'rejections':[2]}):
            for code, error in ((0,{}),(-11,{}),(1,{'operation':'read','message':'missing input'})):
                result=subprocess.CompletedProcess([],code,stdout=json.dumps({'error':error}),stderr='')
                with patch('ci_support.subprocess.run',return_value=result),self.assertRaises(AssertionError):
                    ci_support.rejection('mapac',Path('bundle'),Path('root'),{'cases':[]},{'index':2})

    def test_ci_profile_rejects_skips_and_oversubscribed_workers(self):
        for options in (['--skip','validate_cac'],['--only','validate_cac'],['--jobs','3'],['--shard','0']):
            result=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('run_portable_suite.py')),
                '--profile','ci-fast','--binary','missing','--target','aarch64-apple-darwin',
                '--build-profile','debug','--out','unused',*options],capture_output=True,text=True)
            self.assertEqual(result.returncode,2,result.stderr)

    def test_workflow_requires_gate_for_release_and_runs_it_after_failures(self):
        text=(Path(__file__).resolve().parents[1]/'.github/workflows/build.yml').read_text()
        gate=text.split('\n  consistency:',1)[1].split('\n  release:',1)[0]
        release=text.split('\n  release:',1)[1]
        self.assertIn('needs: [build, full_math, full_collect]',gate)
        self.assertIn('if: always()',gate)
        self.assertIn('needs: [build, consistency]',release)
        self.assertNotIn('always()',release)
        self.assertEqual(text.count("if: github.event_name != 'pull_request'"),2)
        self.assertIn('branches: [main]',text)
        self.assertIn("tags: ['v*']",text)
        self.assertIn('  pull_request:',text)
        self.assertIn('  workflow_dispatch:',text)


if __name__ == '__main__':unittest.main()
