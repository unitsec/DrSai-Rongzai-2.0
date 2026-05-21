##How to build the refineTool to module that python cam import
1. cd /path/to/rongzai-agent/tools/pyZRietveld
2. Use pyZrietveld python executable directly:
	bash scripts/build_pybind_module.sh "$PWD" "$PWD/refineTool.cpp" refineTool "/home/wangh/software/anaconda3/envs/pyZrietveld/bin/python"

3. Or resolve python from conda env dynamically:
	PY_BIN="$(conda run -n pyZrietveld python -c \"import sys; print(sys.executable)\")"
	bash scripts/build_pybind_module.sh "$PWD" "$PWD/refineTool.cpp" refineTool "$PY_BIN"