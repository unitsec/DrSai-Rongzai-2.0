#ifndef ATOM_GEOMETRY_HH_
#define ATOM_GEOMETRY_HH_

#include "../RietveldAnalysisTypes.hh"
#include "../zerror_type/error_out.hh"
#include "../utility_data_structure/topo3.hh"
#include "../utility_func/lattice_constant.hh"

inline Double calAtomDistanceSquare(const VecDat3<Double>& xyz1, const VecDat3<Double>& xyz2, const SymMat3<Double>& S_inv)
{
	return norm(xyz1 - xyz2, S_inv);
}


// On input, the argument atom_pos_covar is the covariant matrix on xyz1, xyz2.
// (Hence, it is a square matrix of size 6.)
Double calAtomDistanceSquare(const VecDat3<Double>& xyz1, const VecDat3<Double>& xyz2, const SymMat3<Double>& S_inv,
		const SymMat<Double>& S_inv_covar, const SymMat<Double>& atom_pos_covar, Double& var);



Double calBondAngle(const VecDat3<Double>& xyz1,
					const VecDat3<Double>& xyz2, const VecDat3<Double>& xyz3, const SymMat3<Double>& S_inv);

Double calBondAngle(const VecDat3<Double>& xyz1,
		const VecDat3<Double>& xyz2, const VecDat3<Double>& xyz3, const SymMat3<Double>& S_inv,
		const SymMat<Double>& S_inv_covar, const SymMat<Double>& atom_pos_covar, Double& var);

Double calAtomDistance(const VecDat3<Double>&, const VecDat3<Double>&, const SymMat3<Double>&);
Double calAtomDistance(const VecDat3<Double>&, const VecDat3<Double>&, const SymMat3<Double>&,
									VecDat3<Double>&, VecDat3<Double>&);
Double calAtomDistance(const VecDat3<Double>&, const VecDat3<Double>&, const SymMat3<Double>&,
									VecDat3<Double>&, VecDat3<Double>&, SymMat3<Double>&);


Double calBondAngle(const VecDat3<Double>&, const VecDat3<Double>&, const VecDat3<Double>&, const SymMat3<Double>&,
									VecDat3<Double>&, VecDat3<Double>&, VecDat3<Double>&);
Double calBondAngle(const VecDat3<Double>&, const VecDat3<Double>&, const VecDat3<Double>&, const SymMat3<Double>&,
									VecDat3<Double>&, VecDat3<Double>&, VecDat3<Double>&, SymMat3<Double>&);

#endif /*ATOM_GEOMETRY_HH_*/
