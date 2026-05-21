/*
 ChemicalSpeciesData.hh
 
 Author: HIRAI Hiroaki, R. Tomiyasu
 
 Copyright 2017-2025 Project Z-Code. All rights reserved.
 */

#pragma once

#include <string>

#include "../zfoundation/zstringstream.hh"
#include "../utility_atomic_data/scattering_factor.hh"

class AtomSiteData;

class ChemicalSpeciesData
{
public:
	string m_symbolXray;
	Int4 m_symbolNeutron;

	ChemicalSpeciesData(){ mRatio = 1.0; }
	
	void SetSymbol(const string symbol){ mSymbol = symbol; }
	const string& GetSymbol() const { return mSymbol; }
	
	void SetRatio(double ratio){ mRatio = ratio; }
	const Double& GetRatio() const { return mRatio; }
	
	ZErrorMessage GetNormalDispersionFactorCoefXray(scattering_coef_Xray& scatteringCoefXray) const;
	
	void DebugDescription(zstringstream& stream) const;

private:
	Double mRatio;
	string mSymbol;
};
