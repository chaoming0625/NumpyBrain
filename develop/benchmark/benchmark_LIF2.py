import sys
sys.path.append('/mnt/d/codes/Projects/BrainPy')

import argparse
import os
from time import time as t

import ANNarchy
import brian2 as b2
import nengo
import nest
import numpy as np
import pandas as pd
import torch

import brainpy as bp

# from experiments.benchmark import plot_benchmark
# figure_path = os.path.abspath('benchmark')
# benchmark_path = os.path.abspath('benchmark')
# if not os.path.isdir(benchmark_path):
#     os.makedirs(benchmark_path)

# "Warm up" the CPU.
torch.set_default_tensor_type("torch.FloatTensor")
x = torch.rand(1000)
del x

dt = 1.0
tau = 10.
V_rest = -74.
V_reset = -60.
V_threshld = -54.


def BrainPy_cpu(n_neurons, time):
    '''
    UNIFY:
    poisson input: Y
    input freq:    15.
    dt:            1.0
    n_neuron:      Y
    conn:          all2all(n_neuron)
    neuron type:   LIF
    neuron params: N
    synapse type:  AMPA
    one thread:    Y
    '''
    bp.profile.set(jit=True, device='cpu', dt=dt, merge_steps=True)

    # neuron

    @bp.integrate
    def int_f(V, t, Isyn):
        return (-V + V_rest + Isyn) / tau

    def neu_update(ST, _t):
        V = int_f(ST['V'], _t, ST['input'])
        if V >= V_threshld:
            ST['spike'] = 1.
            V = V_reset
        else:
            ST['spike'] = 0.
        ST['V'] = V
        ST['input'] = 0.

    lif = bp.NeuType(name='LIF',
                     ST=bp.NeuState({'V': V_reset, 'spike': 0., 'input': 0.}),
                     steps=neu_update,
                     mode='scalar')

    # synapse

    g_max = 0.10
    E = 0.

    def syn_update(pre, post, conn_mat):
        g = g_max * np.dot(pre['spike'], conn_mat)
        post['input'] -= g * (post['V'] - E)

    syn = bp.SynType(name='simple_synapse',
                     ST=bp.types.SynState(['s']),
                     steps=syn_update,
                     mode='matrix')

    pre_neu = bp.inputs.PoissonInput(geometry=(n_neurons,), freqs=15.)
    post_neu = bp.NeuGroup(lif, geometry=(n_neurons,))
    syn = bp.SynConn(syn,
                     pre_group=pre_neu,
                     post_group=post_neu,
                     conn=bp.connect.All2All())
    net = bp.Network(pre_neu, syn, post_neu)

    net.run(duration=time, report=True)




def BRIAN2(n_neurons, time):
    '''
    UNIFY:
    poisson input: Y
    input freq:    15.
    dt:            1.0
    n_neuron:      Y
    conn:          all2all
    neuron type:   LIF
    neuron params: N
    synapse type:  simple
    one thread:    Y
    '''
    t0 = t()

    # b2.set_device('cpp_standalone', build_on_run=False)
    b2.set_device('runtime', build_on_run=False)
    b2.defaultclock = dt * b2.ms
    b2.device.build()

    t1 = t()

    eqs_neurons = """
        dv/dt = (ge * (-60 * mV) + (-74 * mV) - v) / (10 * ms) : volt
        ge : 1
    """

    input = b2.PoissonGroup(n_neurons, rates=15. * b2.Hz)
    neurons = b2.NeuronGroup(
        n_neurons,
        model=eqs_neurons,
        threshold=f"v > {V_threshld} * mV",
        reset=f"v = {V_reset} * mV",
        method="euler",
    )
    S = b2.Synapses(input, neurons,
                    model='w : 1',
                    on_pre=f"ge -= w")
    S.connect(p=1.0)
    S.w = "0.1"

    t2 = t()
    b2.run(time * b2.ms,
           report='stdout',
           report_period=0.05 * b2.second)

    return t() - t0, t() - t1, t() - t2



def PyNEST(n_neurons, time):
    '''
    UNIFY:
    poisson input: Y
    input freq:    15.
    dt:            1.0
    n_neuron:      Y
    conn:          all2all
    neuron type:   LIF
    neuron params: ???
    synapse type:  simple
    one thread:    Y
    '''
    t0 = t()
    nest.ResetKernel()
    nest.SetKernelStatus({"local_num_threads": 1, "resolution": 1.0})
    t1 = t()
    r_ex = 15.0  # [Hz] rate of exc. neurons
    neuron = nest.Create("iaf_psc_alpha", n_neurons)
    nest.SetStatus(neuron,
                   {"V_m": V_reset, 'E_L': V_rest, 'V_reset': V_reset,
                    'C_m': 1., 'tau_m': tau, 'V_th': V_threshld,
                    't_ref': 0.})
    poisson = nest.Create("poisson_generator", n_neurons)
    nest.SetStatus(poisson, [{"rate": r_ex}])
    nest.Connect(poisson, neuron, conn_spec='all_to_all')
    t2 = t()
    nest.Simulate(time)
    return t() - t0, t() - t1, t() - t2


def ANNarchy_cpu(n_neurons,
                 time):  # HH https://annarchy.readthedocs.io/en/latest/example/HodgkinHuxley.html?highlight=HH#Hodgkin-Huxley-neuron
    '''
    UNIFY:
    poisson input: Y
    input freq:    15.
    dt:            1.0
    n_neuron:      Y
    conn:          all2all
    neuron type:   LIF
    neuron params: N
    synapse type:  None--Increases the post-synaptic conductance from the synaptic efficency after each pre-synaptic spike?
    one thread:    Y
    '''
    t0 = t()
    ANNarchy.setup(paradigm="openmp", num_threads=1, dt=dt)
    ANNarchy.clear()

    t1 = t()

    IF = ANNarchy.Neuron(
        parameters=f"""
            tau_m = {tau}
            tau_e = 5.0
            vt = {V_threshld}
            vr = {V_reset}
            El = {V_rest}
            Ee = 0.0
        """,
        equations=f"""
            tau_m * dv/dt = El - v + g_exc *  (Ee - vr) : init = {V_reset}
            tau_e * dg_exc/dt = - g_exc
        """,
        spike="""
            v > vt
        """,
        reset="""
            v = vr
        """,
    )

    Input = ANNarchy.PoissonPopulation(name="Input", geometry=n_neurons, rates=15.0)
    Output = ANNarchy.Population(name="Output", geometry=n_neurons, neuron=IF)
    proj = ANNarchy.Projection(pre=Input,
                               post=Output,
                               target="exc",
                               synapse=None)
    proj.connect_all_to_all(weights=0.1)

    ANNarchy.compile()
    t2 = t()
    ANNarchy.simulate(duration=time, measure_time=True)

    return t() - t0, t() - t1, t() - t2





def Nengo(n_neurons, time):
    '''
    UNIFY:
    poisson input: N(LIF)
    input freq:    ???
    dt:            ???
    n_neuron:      Y
    conn:          ???
    neuron type:   LIF
    neuron params: ???
    synapse type:  ???
    one thread:    ???(cant find)+
    '''
    t0 = t()
    t1 = t()

    model = nengo.Network()
    with model:
        X = nengo.Ensemble(n_neurons, dimensions=n_neurons, neuron_type=nengo.LIF())
        Y = nengo.Ensemble(n_neurons, dimensions=n_neurons, neuron_type=nengo.LIF())
        nengo.Connection(X, Y, transform=np.random.rand(n_neurons, n_neurons))

    t2 = t()
    with nengo.Simulator(model) as sim:
        sim.run(time / 1000)  # dt?

    return t() - t0, t() - t1, t() - t2

    # have Izh but no HH
print(Nengo(1000, 1000))


def write(start=100, stop=1000, step=100, time=1000, name=None, data=None):
    print(data)

    filename = "benchmark_LIF_" + name + f"_{start}_{stop}_{step}_{time}.csv"
    f = os.path.join(benchmark_path, filename)
    if os.path.isfile(f):
        os.remove(f)
    df = pd.DataFrame.from_dict(data)
    df.index = list(range(start, stop + step, step))

    print()
    print(df)
    print()

    df.to_csv(f)


def main(start=100000, stop=100001, step=100, time=1000):
    total_times = {
        "BindsNET_cpu": [],
        "BRIAN2": [],
        "PyNEST": [],
        "ANNarchy_cpu": [],
        'Nengo': [],
        'BrainPy_cpu': []
    }

    build_times = {
        "BindsNET_cpu": [],
        "BRIAN2": [],
        "PyNEST": [],
        "ANNarchy_cpu": [],
        'Nengo': [],
        'BrainPy_cpu': []
    }

    sim_times = {
        "BindsNET_cpu": [],
        "BRIAN2": [],
        "PyNEST": [],
        "ANNarchy_cpu": [],
        'Nengo': [],
        'BrainPy_cpu': []
    }

    for n_neurons in range(start, stop + step, step):
        print(f"\nRunning benchmark with {n_neurons} neurons.")
        for framework in sim_times.keys():
            if n_neurons > 5000 and framework == "ANNarchy_cpu" or \
                    n_neurons > 2500 and framework == "PyNEST":
                total_times[framework].append(np.nan)
                build_times[framework].append(np.nan)
                sim_times[framework].append(np.nan)
                continue

            print(f"- {framework}:", end=" ")

            fn = globals()[framework]
            total, build, sim = fn(n_neurons=n_neurons, time=time)
            total_times[framework].append(total)
            build_times[framework].append(build)
            sim_times[framework].append(sim)

            print(f"(total: {total:.4f}; build: {build:.4f};sim: {sim:.4f})")

    # write(start = start, stop = stop, step = step, time = time, name = 'total', data = total_times)
    # write(start = start, stop = stop, step = step, time = time, name = 'build', data = build_times)
    write(start=start, stop=stop, step=step, time=time, name='sim', data=sim_times)




if __name__ == "__main__1":
    # get params
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=100)
    parser.add_argument("--stop", type=int, default=1000)
    parser.add_argument("--step", type=int, default=100)
    parser.add_argument("--time", type=int, default=1000)
    args = parser.parse_args()

    main(
        start=args.start,
        stop=args.stop,
        step=args.step,
        time=args.time
    )