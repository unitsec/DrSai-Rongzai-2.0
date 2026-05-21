#ifndef DEBYEWALLER_UTIL_HH_
#define DEBYEWALLER_UTIL_HH_

#include"../RietveldAnalysisTypes.hh"
#include"../zerror_type/error_out.hh"
#include"../zparam/ZParawError.hh"
#include"../utility_data_structure/VecDat3.hh"
#include"../utility_data_structure/SymMat3.hh"
#include"../utility_data_structure/SymMat.hh"
#include"../levenberg_marquardt/SVdcmp.hh"
#include "../ZRTSite_.h"

ZRietveldTemperatureFactorUnit GetZRietveldTemperatureFactorUnitWithInt4(Int4 number);

Int4 GetInt4WithZRietveldTemperatureFactorUnit(const ZRietveldTemperatureFactorUnit& temperatureFactorUnit);

inline Int4 PNTempFactor(const bool temp_iso_flag)
{
	if( temp_iso_flag ) return ZRTAtomicDisplacementAnisoSize;
	else return ZRTAtomicDisplacementIsoSize;
}

// This method is called to get the coefficient by transformTempFactorToBeta and transformTempFactorFromBeta.
const Double& putTempFactorCoef(const ZRietveldTemperatureFactorUnit& temperatureFactorUnit);


// Transforms Debye-WallerFactor from B or U to beta.
// beta = 0.25*B.
// beta = 2*pi*pi*U.
// betaij = 0.25*sqrt(S(i,i)*S(j,j))*Bij.
// betaij = 2*pi*pi*sqrt(S(i,i)*S(j,j))*Uij.
void transformTempFactorToBeta(const ZRietveldTemperatureFactorUnit& temperatureFactorUnit, const SymMat3<Double>& S,
							   const bool& tempisoflag, const vector<ZParawError>& BU, vector<ZParawError>& beta);


// Transforms Debye-WallerFactor from beta to B or U.
// betaij = 0.25*sqrt(S(i,i)*S(j,j))*Bij.
// betaij = 2*pi*pi*sqrt(S(i,i)*S(j,j))*Uij.
void transformTempFactorFromBeta(const ZRietveldTemperatureFactorUnit& temperatureFactorUnit, const SymMat3<Double>& S, const SymMat<Double>& S_covar,
										const bool& tempisoflag, const vector<ZParawError>& beta, vector<ZParawError>& BU);

// Errors are set to zero.
void transformTempFactorFromIsoToAniso(const SymMat3<Double>& S,
									   const ZParawError& iso_temp_factor, vector<ZParawError>& aniso_temp_factor);

void transformTempFactorFromIsoToAniso(const SymMat3<Double>& S,
									   const SymMat<Double>& S_covar,
									   const ZParawError& iso_temp_factor, vector<ZParawError>& aniso_temp_factor);


// Changes anisotropic Debye-Waller factors (beta) into isotropic ones (beta).
void transformTempFactorFromAnisoToIso(const SymMat3<Double>& S,
									   const SymMat<Double>& S_covar,
									   const vector<Double>& aniso_temp_factor, const SymMat<Double>& TempFactor_covar,
									   ZParawError& iso_temp_factor);

#endif /*SCATTERING_FACTOR_HH_*/
