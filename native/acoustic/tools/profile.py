"""Matched-dispatch diagnostic: original kernel/workspace vs standalone batch.
Temporary C helpers only; every solve retains identical checked ABI validation.
"""
import ctypes as ct
import json
from pathlib import Path
import resource
import subprocess
import tempfile
import time
from verify import ROOT, Job, Kernel, cases

REPEAT = 100000
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    core = (ROOT/'native/acoustic/core.c').read_text()
    original = (ROOT/'src/dopplergeist/_native_acoustic.c').read_text().split('#define PY_SSIZE_T_CLEAN')[0]
    assert original in core, 'numeric kernel changed: revise diagnostic baseline'
    wrapper = core[core.index('int dg_solve('):core.index('int dg_solve_batch(')]
    wrapper = wrapper.replace('int dg_solve(', 'int dg_reference_solve(', 1)
    wrapper = wrapper.replace('int result = dg_acoustic(', '''uint16_t small_state[64];
 uint16_t *owned = n<=64 ? small_state :
   (n<=DG_MAX_STATE_BYTES/sizeof(uint16_t) ? (uint16_t*)malloc(n*sizeof(uint16_t)) : NULL);
 int result = dg_acoustic(''')
    wrapper = wrapper.replace('factor,tau,distance,state);', 'factor,tau,distance,owned);\n if (owned != small_state) free(owned);')
    reference = tmp/'reference.c'
    reference.write_text('#include "core.h"\n#include <stdlib.h>\n'+original+wrapper)
    declaration = wrapper[:wrapper.index('{')].strip()+';'
    args = 'j->t,j->n,j->observer,j->p,j->plen,j->x,j->m,j->packed,j->packed_len,j->packed_x,j->pm,j->origin,j->start,j->end,j->c,j->arrival_start,j->arrival_end,j->factor,j->tau,j->distance,j->state,j->state_len'
    harness = tmp/'harness.c'
    harness.write_text('#include "core.h"\n'+declaration+'''
int dg_replay(dg_job *jobs, size_t count, size_t repeat, int mode) {
 for (size_t r=0; r<repeat; ++r) {
  if (mode==1) {if(dg_solve_batch(jobs,count)) return 1;}
  else for(size_t i=0;i<count;++i) {
   dg_job *j=jobs+i;
   j->result=mode==0 ? dg_reference_solve('''+args+''') : dg_solve('''+args+''');
   if(j->result) return 1;
  }
 }
 return 0;
}
''')
    libpath = tmp/'profile.so'
    subprocess.run(['gcc','-O3','-ffp-contract=off','-fno-fast-math','-shared','-fPIC','-I',str(ROOT/'native/acoustic'),str(ROOT/'native/acoustic/core.c'),str(reference),str(harness),'-lm','-o',str(libpath)],check=True)
    lib = ct.CDLL(str(libpath))
    replay = lib.dg_replay
    replay.argtypes = [ct.POINTER(Job),ct.c_size_t,ct.c_size_t,ct.c_int]
    replay.restype = ct.c_int
    kernel = Kernel(tmp)
    data = cases()
    work = [kernel.prepare(a,s) for _,a,s,_ in data]
    jobs = (Job*len(work))(*(Job(*call,0) for out,state,call in work))
    for mode in range(3):
        assert replay(jobs,len(jobs),1,mode)==0
        for (_,a,s,_),(out,state,call) in zip(data,work):
            status,expected = kernel.run(a,s)
            assert status==0
            for x,y in zip(out,expected): assert x.tobytes()==y.tobytes()
    runs = []
    for pair in range(3):
        for mode in ((0,1,2) if pair%2==0 else (2,1,0)):
            cpu = time.process_time()
            wall = time.perf_counter()
            assert replay(jobs,len(jobs),REPEAT,mode)==0
            runs.append(dict(pair=pair,mode=['original_C_kernel_workspace','standalone_batch','standalone_scalar'][mode],wall_seconds=time.perf_counter()-wall,cpu_seconds=time.process_time()-cpu,peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))
    print(json.dumps(dict(repeat=REPEAT,trajectories=len(data),samples=sum(len(a[0]) for _,a,_,_ in data),runs=runs),indent=2))
