/*
 ChemicalSpeciesData.cc

 Author: HIRAI Hiroaki, R. Tomiyasu
 
 Copyright 2017-2025 Project Z-Code. All rights reserved.
 */

#include "ChemicalSpeciesData.hh"

ZErrorMessage ChemicalSpeciesData::GetNormalDispersionFactorCoefXray(scattering_coef_Xray& scatteringCoefXray) const
{
	return read_normal_dispersion_factor_coef_Xray(m_symbolXray, scatteringCoefXray);
}

void ChemicalSpeciesData::DebugDescription(zstringstream& stream) const
{
	stream
	<< "Chemical species symbol x-ray = " << m_symbolXray << endl
	<< "Chemical species symbol neutron = " << m_symbolNeutron << endl
	<< "Chemical species ratio = " << mRatio << endl;
}
