'''
Yang Yunjia @ Nov. 23 2024

This file is modified from 
'''


import os, sys, random, time
import numpy as np
import json
from adflow import ADFLOW
from baseclasses import AeroProblem
from mpi4py import MPI

comm = MPI.COMM_WORLD


def _json_safe_scalar(value):
    """Return a JSON-safe scalar without silently stringifying solver data."""
    if isinstance(value, (bool, str, int, float)) or value is None:
        return value
    if isinstance(value, np.generic):
        return value.item()
    return None


def _solvecl_summary(result):
    """Persist the load-bearing solveCL verdict without its large history arrays."""
    if not isinstance(result, dict):
        return {"available": False, "return_type": type(result).__name__}
    keys = ("converged", "iterations", "l2convergence", "alpha", "cl",
            "clstar", "error", "clalpha", "time")
    summary = {key: _json_safe_scalar(result.get(key)) for key in keys}
    history = result.get("history")
    summary["history_entries"] = len(history) if isinstance(history, (list, tuple)) else None
    summary["available"] = True
    return summary

def redirect_stdout(outFile):
    def decorator(func):
        def wrapper(*args, **kwargs):
            original_stdout_fd = os.dup(sys.stdout.fileno())
            # original_stdout = sys.stdout
            with open(outFile, 'w') as fstdout:

                os.dup2(fstdout.fileno(), sys.stdout.fileno())
                try:
                    result = func(*args, **kwargs)
                finally:
                    os.dup2(original_stdout_fd, sys.stdout.fileno())
                    os.close(original_stdout_fd)
                
            return result
            
        return wrapper
    return decorator

@redirect_stdout('adflow.log')
def run_ADflow(grid, output, wingCoefs, conds, CL_targ = None, alpha0 = None):
    if not os.path.exists(output):
        if comm.rank == 0:
            os.mkdir(output)

    # rst ADflow options
    aeroOptions = {
        # I/O Parameters
        "gridFile": grid,
        "outputDirectory": output,
        "monitorvariables": ["resrho", "cl", "cd", "cmz", "resturb"],
        "writeTecplotSurfaceSolution": True,
        "surfaceVariables": ['cp', 'rho', 'temp', 'ptloss', 'vx', 'vy', 'vz', 'mach', 'cfx', 'cfy', 'cfz', 'ch', 'yplus'],
        # Physics Parameters
        "equationType": "RANS",
        # Solver Parameters
        "MGCycle": "3w",
        # ANK Solver Parameters
        # ADFLOW_USE_ANK=0 -> NK-only (no ANK pseudo-transient warmup; base multigrid startup
        # until NKSwitchTol, then NK). Tests whether NK alone converges + how fast vs ANK->NK.
        "useANKSolver": os.environ.get("ADFLOW_USE_ANK", "1") == "1",
        # 'ankcoupledswitchtol': 1e-6,    # start to coupling solve turbulence variables
        # NK Solver Parameters
        # The vendored release shipped useNKSolver=False (ANK-only), but the SuperWing PAPER
        # (Sec 2.3.1) describes ANK->NK: "once the total residual drops below 1e-8, the solver
        # switches to NK ... convergence declared at 1e-10". ANK-only stalls in the last orders
        # on stiff transonic cases -> pessimistic non-convergence + run-to-run verdict flips at
        # the buffet margin (docs/cfd/CFD_RESULTS.md). Enabling NK matches the PAPER and is being
        # tested as a fix for our convergence non-determinism. ADFLOW_USE_NK=0 reverts to the
        # vendored ANK-only for an A/B. (Open author question: did the DATASET use NK? the paper
        # and the released script disagree -- so NK-on is a faithfulness bet on the paper.)
        "useNKSolver": os.environ.get("ADFLOW_USE_NK", "1") == "1",
        "NKSwitchTol": float(os.environ.get("ADFLOW_NK_SWITCH", "1e-8")),  # raise to engage NK earlier
        # Termination Criteria
        "L2Convergence": 1e-10,
        "nCyclesCoarse": 500,
        "nCycles": 3000,
    }
    # rst Start ADflow
    # Create solver
    CFDSolver = ADFLOW(comm=comm, options=aeroOptions)

    # Add features
    CFDSolver.addLiftDistribution(150, "z")
    # CFDSolver.addSlices("z", np.linspace(0.11 * wingCoefs['half_span'], wingCoefs['half_span'], 10))

    # rst Create AeroProblem
    ap = AeroProblem(name="wing", mach=conds['M3'], alpha=conds['AA'], reynolds=conds['re'], reynoldsLength=1., T=conds['temp'],
                     areaRef=wingCoefs['surface_area'], chordRef=1., xRef=0.25,
                     evalFuncs=["cl", "cd", "cmx", "cmy", "cmz"])
    # rst Run ADflow
    
    # Solve
    solvecl_result = None
    if CL_targ is None:
        CFDSolver(ap)
    else:
        # Every solveCL option is explicit. These values are part of the grading
        # protocol and must not drift with an ADflow library-default change.
        solvecl_result = CFDSolver.solveCL(
            ap,
            CLStar=CL_targ,
            alpha0=alpha0,
            CLalphaGuess=None,
            delta=0.5,
            tol=0.001,
            autoReset=False,
            maxIter=20,
            relaxCLa=1.0,
            relaxAlpha=1.0,
            L2ConvRel=None,
            stopOnStall=False,
            writeSolution=True,
            workUnitTime=None,
            updateCutoff=1e-16,
        )
    # rst Evaluate and print
    funcs = {}
    CFDSolver.evalFunctions(ap, funcs)
    funcs["_grading"] = {
        "contract_version": "superwing-adflow-evidence-v1",
        "solvecl_requested": CL_targ is not None,
        "solvecl": _solvecl_summary(solvecl_result),
        "aeroproblem": {
            "solveFailed": _json_safe_scalar(getattr(ap, "solveFailed", None)),
            "fatalFail": _json_safe_scalar(getattr(ap, "fatalFail", None)),
        },
    }
    if isinstance(solvecl_result, dict) and solvecl_result.get("alpha") is not None:
        funcs["AA"] = _json_safe_scalar(solvecl_result["alpha"])
        
    return funcs

def read_adflow_log(func_dict, logFile : str = 'adflow.log', n: int = 10):
    '''
    `n`:    large step
    
    '''

    with open(logFile, 'r') as f:
        lines = f.readlines()
    
    # cl calculation update aoa
    for i in range(len(lines)):
        if len(lines[i].split()) > 0 and lines[i].split()[0] == "{'alpha':":
            func_dict['AA'] = float(lines[i].split()[1][:-1])
            print('Alpha...', func_dict['AA'])
            break
        
    for i in range(len(lines)):
        if len(lines[i].split()) > 1 and lines[i].split()[0] == '#' and lines[i].split()[1] == 'Grid':
            if len(lines[i+1].split()) > 1 and lines[i+1].split()[0] == '#' and lines[i+1].split()[1] == 'level':
                break
    else:
        print('Not find log')
        return func_dict
    
    to_float = lambda x: np.nan if x == b'----' else float(x)
    flow_history = np.loadtxt(logFile, converters={4: to_float, 5: to_float}, 
                              comments=['#', '|', '+-', '->', "{\'", "\'"], skiprows=i, usecols=(2, 4, 5, 7, 8, 9, 10, 11, 12))  # Step number, CFL, Step length, Res Rho, C_lift, C_drag, totalRes
    func_dict['err_cl'] = (np.max(flow_history[-n:, -4]) - np.min(flow_history[-n:, -4]))
    func_dict['err_cd'] = (np.max(flow_history[-n:, -3]) - np.min(flow_history[-n:, -3]))
    func_dict['err_cmz'] = (np.max(flow_history[-n:, -2]) - np.min(flow_history[-n:, -2]))
    func_dict['n_step'] = flow_history[-1, 0]
    func_dict['min_ResRho'] = np.min(flow_history[-n:, 3])
    # ``flow_history`` has nine selected columns; totalRes is the final column.
    # The historical index 6 pointed at C_drag and silently mislabeled it.
    func_dict['min_totalRes'] = np.min(flow_history[-n:, -1])
    func_dict['min_relative_ResRho'] = np.min(flow_history[-n:, 3]) / np.min(flow_history[0, 3])
    func_dict['min_relative_totalRes'] = np.min(flow_history[-n:, -1]) / np.min(flow_history[0, -1])
        
    return func_dict

if __name__ == "__main__":

    freestream_temperature = 300    # K
    reynold_number = 20.e6          # Million

    #*--------------------------------------
    #* Read Wing Coefficient
    
    curr_dir = os.getcwd()

    with open('conds.json', 'r') as f:
        time.sleep(random.random())     # simutanously reading json may cause error
        conds = json.load(f)
    with open('../input.json', 'r') as f:
        time.sleep(random.random())     # simutanously reading json may cause error
        wingCoefs = json.load(f)
    
    funcs_dict = {}
        
    #*--------------------------------------
    t3 = time.perf_counter()
    
    funcs_dict = run_ADflow(grid="../wing_vol.cgns", output=".", wingCoefs=wingCoefs, conds=conds,
                            CL_targ=conds.get("CL_targ"), alpha0=conds.get("AA"))
    
    if comm.rank == 0:
        # Print the evaluated functions 
        funcs_dict = read_adflow_log(funcs_dict)
        
        with open('output.json', 'w') as f:
            json.dump({'results': funcs_dict, 'working_conditions': conds}, f)

    
    
