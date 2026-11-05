"""Real archived trajectory differential checks and bounded kernel benchmarks.
Run from repository root with PYTHONPATH=src. No audio/location validation.
"""
import argparse
import ctypes as ct
import json
import os
from pathlib import Path
import resource
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from dopplergeist import geometry, native_acoustic
from dopplergeist.tracks import _segment_velocity
ROOT = Path(__file__).resolve().parents[3]
D = ct.POINTER(ct.c_double)
U = ct.POINTER(ct.c_uint16)

def cases():
    result = []
    for source in sorted((ROOT/'research/trajectory-motion/frozen').glob('fresh-*.json')):
        frame = pd.DataFrame(json.loads(source.read_text())).sort_values('time_s')
        for number, (_, rows) in enumerate(frame.groupby((frame.time_s.diff()>30).cumsum())):
            if len(rows)<3: continue
            velocity, _ = _segment_velocity(rows)
            track = geometry.Track(str(rows.flight_id.iloc[0]), rows.time_s.to_numpy(),
                rows.lat.to_numpy(), rows.lon.to_numpy(), rows.alt_m.to_numpy(),
                str(rows.typecode.iloc[0]), velocity)
            try: track.validate_motion_support()
            except geometry.AcousticSupportError: continue
            observer = geometry.ecef(track.lat[0], track.lon[0], 0.)
            receive = track.time+np.linalg.norm(track.xyz-observer,axis=1)/343.
            packed = geometry._packed_kinematics(track.spline,track.derivative)
            args = [np.ascontiguousarray(a,dtype=np.float64) for a in
                (receive,observer,track.spline.c,track.spline.x,packed.c,packed.x)]
            scalars = [float(v) for v in (track.origin,track.time[0],track.time[-1],343.,receive[0],receive[-1])]
            result.append((track,args,scalars,source.name))
    if not result: raise RuntimeError('no real supported trajectories')
    return result

class Job(ct.Structure):
    _fields_=list(zip(['t','n','observer','p','plen','x','m','packed','packed_len','packed_x','pm','origin','start','end','c','arrival_start','arrival_end','factor','tau','distance','state','state_len'],[D,ct.c_size_t,D,D,ct.c_size_t,D,ct.c_size_t,D,ct.c_size_t,D,ct.c_size_t]+[ct.c_double]*6+[D,D,D,U,ct.c_size_t]))+[('result',ct.c_int)]

class Kernel:
    def __init__(self, directory):
        binary = Path(os.environ.get('DG_ACOUSTIC_LIB', str(Path(directory)/'libdg.so')))
        if not os.environ.get('DG_ACOUSTIC_LIB'):
            subprocess.run(['gcc','-O3','-ffp-contract=off','-fno-fast-math','-shared','-fPIC',str(ROOT/'native/acoustic/core.c'),'-lm','-o',str(binary)],check=True)
        self.lib=ct.CDLL(str(binary)); self.solve=self.lib.dg_solve
        self.solve.argtypes=[D,ct.c_size_t,D,D,ct.c_size_t,D,ct.c_size_t,D,ct.c_size_t,D,ct.c_size_t]+[ct.c_double]*6+[D,D,D,U,ct.c_size_t]
        self.solve.restype=ct.c_int
        self.batch=self.lib.dg_solve_batch
        self.batch.argtypes=[ct.POINTER(Job),ct.c_size_t];self.batch.restype=ct.c_int
    def prepare(self,args,scalars):
        t,o,p,x,packed,px=args
        output=[np.empty(t.shape) for _ in range(3)]
        state=np.empty(t.size,dtype=np.uint16)
        ptr=lambda a:a.ctypes.data_as(D)
        call=[ptr(t),t.size,ptr(o),ptr(p),p.size,ptr(x),x.size-1,ptr(packed),packed.size,ptr(px),px.size-1,*scalars,*map(ptr,output),state.ctypes.data_as(U),state.size]
        return output,state,call
    def run(self,args,scalars):
        out,state,call=self.prepare(args,scalars)
        return self.solve(*call),out

def export(case,path):
    track,args,s,source=case
    keys=['times','observer','position','knots','packed','packed_knots']
    r={k:a.ravel().tolist() for k,a in zip(keys,args)}
    r.update(dict(zip(['origin','start','end','c','arrival_start','arrival_end'],s)))
    Path(path).write_text(json.dumps([r],allow_nan=False)+'\n')
    expected=track.acoustic(args[0],args[1],s[3])
    golden={'source': 'research/trajectory-motion/frozen/'+source, 'flight_id':track.id, 'bits':[a.view(np.uint64).tolist() for a in expected]}
    Path(path).with_suffix('.expected.json').write_text(json.dumps(golden)+'\n')

class Parity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.kernel=Kernel(cls.tmp.name);cls.cases=cases()
        if native_acoustic._native is None: raise RuntimeError('build upstream extension first')
    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()
    def compare(self,args,s):
        expected=[np.empty(args[0].shape) for _ in range(3)]
        ok=native_acoustic._native.solve(*args,*s,*expected)
        status,actual=self.kernel.run(args,s)
        self.assertEqual(status==0,ok)
        if ok:
            for a,b in zip(expected,actual):
                self.assertEqual(a.tobytes(),b.tobytes())
    def test_batch_all_outputs_and_statuses(self):
        work=[self.kernel.prepare(a,s) for _,a,s,_ in self.cases]
        jobs=(Job*len(work))(*(Job(*call,0) for out,state,call in work))
        self.assertEqual(self.kernel.batch(jobs,len(jobs)),0)
        for (_,a,s,_),(out,state,call) in zip(self.cases,work):
            status,expected=self.kernel.run(a,s);self.assertEqual(status,0)
            for x,y in zip(expected,out):self.assertEqual(x.tobytes(),y.tobytes())
        jobs[0].state_len=0
        self.assertEqual(self.kernel.batch(jobs,len(jobs)),2)
        self.assertEqual(jobs[0].result,1)
        self.assertTrue(all(j.result==0 for j in jobs[1:]))
    def test_real_native_bitwise(self):
        for _,a,s,_ in self.cases:self.compare(a,s)
    def test_boundaries_and_global_stop(self):
        for _,a,s,_ in self.cases:
            ends=np.array(s[-2:]);q=np.r_[np.nextafter(ends,-np.inf),ends,np.nextafter(ends,np.inf),a[0]]
            self.compare([q,*a[1:]],s)
    def test_nonfinite_queries_fallback(self):
        for value in (np.nan,np.inf,-np.inf):
            _,a,s,_=self.cases[0];self.compare([np.r_[a[0],value],*a[1:]],s)
    def test_reversed_queries_and_sound_speeds(self):
        for _,a,s,_ in self.cases:
            for speed in (338.,343.,348.):
                changed=list(s);changed[3]=speed
                self.compare([a[0][::-1].copy(),*a[1:]],changed)
    def test_signed_zero_query(self):
        _,a,s,_=self.cases[0]
        self.compare([np.r_[-0.,0.,a[0]],*a[1:]],s)
    def test_extreme_sound_speed_fallback(self):
        _,a,s,_=self.cases[0];changed=list(s);changed[3]=1e-312
        self.compare(a,changed)
    def test_python_reference(self):
        for track,a,s,_ in self.cases:
            with patch.object(native_acoustic,'_native',None):expected=track.acoustic(a[0],a[1],s[3])
            status,actual=self.kernel.run(a,s);self.assertEqual(status,0)
            for x,y in zip(expected,actual):self.assertEqual(x.tobytes(),y.tobytes())
    def test_invalid_knots(self):
        _,a,s,_=self.cases[0];a=list(a);a[3]=a[3].copy();a[3][1]=a[3][0]
        self.assertEqual(self.kernel.run(a,s)[0],1)
    def test_bad_parameters(self):
        _,a,s,_=self.cases[0]
        for index,value in ((3,0.),(3,np.nan),(0,np.inf),(4,np.nan)):
            changed=list(s);changed[index]=value;self.assertEqual(self.kernel.run(a,changed)[0],1)
    def test_empty_query(self):
        _,a,s,_=self.cases[0];self.assertEqual(self.kernel.run([a[0][:0],*a[1:]],s)[0],1)
    @unittest.skipUnless(os.environ.get('DG_ACOUSTIC_BIN'), 'set DG_ACOUSTIC_BIN for CLI tests')
    def test_cli_batch_and_nulls(self):
        fixture=json.loads((ROOT/'native/acoustic/real-request.json').read_text())
        r=fixture[0];r['times']=[float(np.nextafter(r['arrival_start'],-np.inf)),r['arrival_start']]
        path=Path(self.tmp.name)/'batch.json';path.write_text(json.dumps([r,r]))
        outputs=[]
        for mode in ('scalar','batch'):
            output=subprocess.check_output([os.environ['DG_ACOUSTIC_BIN'],'-input',str(path),'-repeat','3','-dispatch',mode],text=True)
            result=json.loads(output)
            self.assertEqual(len(result['results']),2)
            self.assertIsNone(result['results'][0]['factor'][0])
            self.assertEqual(result['repeats'],3)
            self.assertEqual(result['dispatch'],mode)
            outputs.append(result['results'])
        self.assertEqual(outputs[0],outputs[1])
    @unittest.skipUnless(os.environ.get('DG_ACOUSTIC_BIN'), 'set DG_ACOUSTIC_BIN for CLI tests')
    def test_cli_rejects_bad_input(self):
        for text in ('[]', '{}', '[{"unknown":1}]', '[null]', '[] trailing'):
            path=Path(self.tmp.name)/'bad.json';path.write_text(text)
            run=subprocess.run([os.environ['DG_ACOUSTIC_BIN'],'-input',str(path)],capture_output=True)
            self.assertNotEqual(run.returncode,0)
    def test_insufficient_workspace(self):
        _,a,s,_=self.cases[0];out,state,call=self.kernel.prepare(a,s);call[-1]=0
        self.assertEqual(self.kernel.solve(*call),1)

def benchmark(repeat):
    with tempfile.TemporaryDirectory() as tmp:
        kernel=Kernel(tmp);data=cases();work=[]
        for _,a,s,_ in data:
            out,state,call=kernel.prepare(a,s)
            # Keep buffers and ctypes pointers alive outside timer.
            work.append((a,s,out,state,call))
        jobs=(Job*len(work))(*(Job(*call,0) for a,s,out,state,call in work))
        runs=[]
        for pair in range(3):
            for backend in (('native_c','standalone','batch') if pair%2==0 else ('batch','standalone','native_c')):
                cpu=time.process_time();wall=time.perf_counter()
                for _ in range(repeat):
                    if backend=='batch':
                        if kernel.batch(jobs,len(jobs))!=0:raise RuntimeError('batch fallback')
                        continue
                    for a,s,out,state,call in work:
                        ok=(native_acoustic._native.solve(*a,*s,*out) if backend=='native_c' else kernel.solve(*call)==0)
                        if not ok:raise RuntimeError('benchmark fallback')
                runs.append(dict(pair=pair,backend=backend,wall_seconds=time.perf_counter()-wall,cpu_seconds=time.process_time()-cpu,peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))
        print(json.dumps(dict(repeat=repeat,trajectories=len(data),samples=sum(len(a[0]) for _,a,_,_ in data),runs=runs),indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--export');parser.add_argument('--export-batch');parser.add_argument('--benchmark',type=int);args=parser.parse_args()
    if args.export_batch:
        requests=[]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'request.json'
            for case in cases():
                export(case,path);requests.extend(json.loads(path.read_text()))
        Path(args.export_batch).write_text(json.dumps(requests,allow_nan=False)+'\n')
    elif args.export:export(cases()[0],args.export)
    elif args.benchmark:benchmark(args.benchmark)
    else:unittest.main(argv=['verify.py'])
