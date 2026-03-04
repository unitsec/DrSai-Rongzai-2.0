#!/bin/bash


# nohup conda run -n gsas2_py311 --live-stream python $PWD/tools/GSAS-II-master/GSASII/0neutron_gsas_refine.py > output_gsas_refine.log 2>&1 &

# nohup conda run -n diffpy.pdffit2_env --live-stream python $PWD/tools/pdf_refinement/0pdf_refine.py > output_pdf_refine.log 2>&1 &
# ps -ef | grep python
#source ~/.bashrc
source /home/dur/miniconda3/etc/profile.d/conda.sh

conda activate gsas2_py311
nohup  python "$PWD/tools/GSAS-II-master/GSASII/0neutron_gsas_refine.py" > output_gsas_refine.log 2>&1 &
nohup python "$PWD/tools/GSAS-II-master/GSASII/1simulate_mcif.py" > output_simulate_cif.log 2>&1 &

conda activate diffpy.pdffit2_env
nohup python "$PWD/tools/pdf_refinement/0pdf_refine.py" > output_pdf_refine.log 2>&1 &

conda activate AIndex_py310
nohup python "$PWD/tools/AIndex/0AIndex_prediction.py" > output_AIndex_prediction.log 2>&1 &