#include "DebyeWaller_util.hh"
#include "../ZRTConstants_.h"

ZRietveldTemperatureFactorUnit GetZRietveldTemperatureFactorUnitWithInt4(Int4 number)
{
	if(0 == number){
		return kZRietveldTemperatureFactorUnitB;
	}
	return kZRietveldTemperatureFactorUnitU;
}

Int4 GetInt4WithZRietveldTemperatureFactorUnit(const ZRietveldTemperatureFactorUnit& temperatureFactorUnit)
{
	if(kZRietveldTemperatureFactorUnitB == temperatureFactorUnit){
		return 0;
	}
	else if(kZRietveldTemperatureFactorUnitU == temperatureFactorUnit){
		return 1;
	}
	return 2;
}

const Double& putTempFactorCoef(const ZRietveldTemperatureFactorUnit& temperatureFactorUnit)
{
	static const Double CoefB = 0.25;
	static const Double CoefU = 2.0*ZRTConstantPi*ZRTConstantPi;	// 2*pi*pi
	
	if(kZRietveldTemperatureFactorUnitU == temperatureFactorUnit){
		return CoefU;
	}
	else if(kZRietveldTemperatureFactorUnitB == temperatureFactorUnit){
		return CoefB;
	}
	throw nerror_out_range("temperatureFactorUnit", temperatureFactorUnit, __FILE__, __LINE__, __FUNCTION__ );
	return CoefB;
}

void transformTempFactorToBeta(const ZRietveldTemperatureFactorUnit& temperatureFactorUnit, const SymMat3<Double>& S,
							   const bool& tempisoflag, const vector<ZParawError>& BU, vector<ZParawError>& beta)
{
	assert( (Int4)BU.size() == PNTempFactor(tempisoflag) );
	
	if(tempisoflag) beta.resize(ZRTAtomicDisplacementAnisoSize);
	else beta.resize(ZRTAtomicDisplacementIsoSize);
	
	Double coef = putTempFactorCoef(temperatureFactorUnit);
	
	if(tempisoflag)
	{
		beta[0] = BU[0].value * S(0,0) * coef;
		beta[1] = BU[1].value * S(1,1) * coef;
		beta[2] = BU[2].value * S(2,2) * coef;
		beta[3] = BU[3].value * sqrt(S(0,0)*S(1,1)) * coef;
		beta[4] = BU[4].value * sqrt(S(0,0)*S(2,2)) * coef;
		beta[5] = BU[5].value * sqrt(S(1,1)*S(2,2)) * coef;
	}
	else
	{
		beta[0] = BU[0].value * coef;
	}
}

void transformTempFactorFromBeta(const ZRietveldTemperatureFactorUnit& temperatureFactorUnit, const SymMat3<Double>& S, const SymMat<Double>& S_covar,
								 const bool& tempisoflag, const vector<ZParawError>& beta, vector<ZParawError>& BU)
{
	assert( (Int4)beta.size() == PNTempFactor(tempisoflag) );
	
	BU.resize(PNTempFactor(tempisoflag));
	
	const Double coef = 1.0/putTempFactorCoef(temperatureFactorUnit);
	
	if(tempisoflag)
	{
		const Double inv_S11 = 1.0 / S(0,0);
		const Double inv_S22 = 1.0 / S(1,1);
		const Double inv_S33 = 1.0 / S(2,2);
		const Double inv_sq_S11S22 = 1.0 / sqrt(S(0,0)*S(1,1));
		const Double inv_sq_S11S33 = 1.0 / sqrt(S(0,0)*S(2,2));
		const Double inv_sq_S22S33 = 1.0 / sqrt(S(1,1)*S(2,2));
		
		BU[0].value = beta[0].value * inv_S11 * coef;
		BU[1].value = beta[1].value * inv_S22 * coef;
		BU[2].value = beta[2].value * inv_S33 * coef;
		BU[3].value = beta[3].value * inv_sq_S11S22 * coef;
		BU[4].value = beta[4].value * inv_sq_S11S33 * coef;
		BU[5].value = beta[5].value * inv_sq_S22S33 * coef;
		
		// sqrt( Sii*Sjj ) times the variance of betaij / sqrt( Sii*Sjj ).
		Double t = beta[0].value*inv_S11, u;
		BU[0].error = beta[0].error*beta[0].error + S_covar(0,0)*t*t;
		t = beta[1].value*inv_S22;
		BU[1].error = beta[1].error*beta[1].error + S_covar(1,1)*t*t;
		t = beta[2].value*inv_S33;
		BU[2].error = beta[2].error*beta[2].error + S_covar(2,2)*t*t;
		t = beta[3].value*inv_S11;
		u = beta[3].value*inv_S22;
		BU[3].error = beta[3].error*beta[3].error + 0.25*( S_covar(0,0)*t*t + S_covar(1,1)*u*u + 2.0*S_covar(0,1)*t*u );
		t = beta[4].value*inv_S11;
		u = beta[4].value*inv_S33;
		BU[4].error = beta[4].error*beta[4].error + 0.25*( S_covar(0,0)*t*t + S_covar(2,2)*u*u + 2.0*S_covar(0,2)*t*u );
		t = beta[5].value*inv_S22;
		u = beta[5].value*inv_S33;
		BU[5].error = beta[5].error*beta[5].error + 0.25*( S_covar(1,1)*t*t + S_covar(2,2)*u*u  + 2.0*S_covar(1,2)*t*u );
		
		// the variance of B/U.
		BU[0].error = sqrt(BU[0].error) * inv_S11 * coef;
		BU[1].error = sqrt(BU[1].error) * inv_S22 * coef;
		BU[2].error = sqrt(BU[2].error) * inv_S33 * coef;
		BU[3].error = sqrt(BU[3].error) * inv_sq_S11S22 * coef;
		BU[4].error = sqrt(BU[4].error) * inv_sq_S11S33 * coef;
		BU[5].error = sqrt(BU[5].error) * inv_sq_S22S33 * coef;
	}
	else
	{
		BU[0].value = beta[0].value * coef;
		BU[0].error = beta[0].error * coef;
	}
}

void transformTempFactorFromIsoToAniso(const SymMat3<Double>& S,
									   const ZParawError& iso_temp_factor, vector<ZParawError>& aniso_temp_factor)
{
	aniso_temp_factor.resize(ZRTAtomicDisplacementAnisoSize);
	
	aniso_temp_factor[0] = S(0,0) * iso_temp_factor.value;
	aniso_temp_factor[1] = S(1,1) * iso_temp_factor.value;
	aniso_temp_factor[2] = S(2,2) * iso_temp_factor.value;
	aniso_temp_factor[3] = S(0,1) * iso_temp_factor.value;
	aniso_temp_factor[4] = S(0,2) * iso_temp_factor.value;
	aniso_temp_factor[5] = S(1,2) * iso_temp_factor.value;
}

void transformTempFactorFromIsoToAniso(const SymMat3<Double>& S,
									   const SymMat<Double>& S_covar,
									   const ZParawError& iso_temp_factor, vector<ZParawError>& aniso_temp_factor)
{
	aniso_temp_factor.clear();
	aniso_temp_factor.resize(ZRTAtomicDisplacementAnisoSize);
	
	aniso_temp_factor[0].value = iso_temp_factor.value * S(0,0);
	aniso_temp_factor[1].value = iso_temp_factor.value * S(1,1);
	aniso_temp_factor[2].value = iso_temp_factor.value * S(2,2);
	aniso_temp_factor[3].value = iso_temp_factor.value * S(0,1);
	aniso_temp_factor[4].value = iso_temp_factor.value * S(0,2);
	aniso_temp_factor[5].value = iso_temp_factor.value * S(1,2);
	
	Double t;
	t = iso_temp_factor.error * S(0,0);
	aniso_temp_factor[0].error = sqrt( iso_temp_factor.value * iso_temp_factor.value * S_covar(0,0) + t*t );
	t = iso_temp_factor.error * S(1,1);
	aniso_temp_factor[1].error = sqrt( iso_temp_factor.value * iso_temp_factor.value * S_covar(1,1) + t*t );
	t = iso_temp_factor.error * S(2,2);
	aniso_temp_factor[2].error = sqrt( iso_temp_factor.value * iso_temp_factor.value * S_covar(2,2) + t*t );
	t = iso_temp_factor.error * S(0,1);
	aniso_temp_factor[3].error = sqrt( iso_temp_factor.value * iso_temp_factor.value * S_covar(5,5) + t*t );
	t = iso_temp_factor.error * S(0,2);
	aniso_temp_factor[4].error = sqrt( iso_temp_factor.value * iso_temp_factor.value * S_covar(4,4) + t*t );
	t = iso_temp_factor.error * S(1,2);
	aniso_temp_factor[5].error = sqrt( iso_temp_factor.value * iso_temp_factor.value * S_covar(3,3) + t*t );
}

void transformTempFactorFromAnisoToIso(const SymMat3<Double>& S,
									   const SymMat<Double>& S_covar,
									   const vector<Double>& aniso_temp_factor, const SymMat<Double>& TempFactor_covar,
									   ZParawError& iso_temp_factor)
{
	iso_temp_factor.value = aniso_temp_factor[0] / S(0,0) + aniso_temp_factor[1] / S(1,1) + aniso_temp_factor[2] / S(2,2);
	
	// Calculate the variance.
	Double t0 = 1.0 / S(0,0);
	Double t1 = 1.0 / S(1,1);
	Double t2 = 1.0 / S(2,2);
	iso_temp_factor.error = TempFactor_covar(0,0)*t0*t0 + TempFactor_covar(1,1)*t1*t1 + TempFactor_covar(2,2)*t2*t2
	+ ( TempFactor_covar(0,1)*t0*t1 + TempFactor_covar(0,2)*t0*t2 + TempFactor_covar(1,2)*t1*t2 ) * 2.0;
	
	t0 = aniso_temp_factor[0] / ( S(0,0)*S(0,0) );
	t1 = aniso_temp_factor[1] / ( S(1,1)*S(1,1) );
	t2 = aniso_temp_factor[2] / ( S(2,2)*S(2,2) );
	iso_temp_factor.error += S_covar(0,0)*t0*t0 + S_covar(1,1)*t1*t1 + S_covar(2,2)*t2*t2
	+ ( S_covar(0,1)*t0*t1 + S_covar(0,2)*t0*t2 + S_covar(1,2)*t1*t2 ) * 2.0;
	
	iso_temp_factor.value /= 3.0;
	iso_temp_factor.error = sqrt(iso_temp_factor.error) / 3.0;
}
