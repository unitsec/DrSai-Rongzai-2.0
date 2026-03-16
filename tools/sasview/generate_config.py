from sasmodels.data import load_data
from sasmodels.core import load_model
from sasmodels.bumps_model import Model, Experiment
from bumps.names import *

shape = "sphere"
kernel = load_model(shape)
para = {}
model = Model(kernel, *para)
# M = Experiment(data=radial_data, model=model, cutoff=1e-6)
# problem = FitProblem(M)
# fit(problem, method="lm")
# pars = model.parameters()
# for name, param in pars.items():
#     print(f"{name}: {param.value}")
status = model.state()
for name, param in status.items():
    print(f"{name}: {param}")