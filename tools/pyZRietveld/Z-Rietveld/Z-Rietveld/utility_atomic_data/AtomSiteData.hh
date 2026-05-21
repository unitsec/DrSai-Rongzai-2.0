/*
 AtomSiteData.hh
 
 Author: HIRAI Hiroaki, R. Tomiyasu
 
 Copyright 2010-2025 Project Z-Code. All rights reserved.
 */

#pragma once

#include <memory>
#include <string>
#include <assert.h>

#include "../ZRTSpaceGroup_.h"

#include "../RietveldAnalysisTypes.hh"
#include "../utility_data_structure/index_set.hh"
#include "../utility_data_structure/SymMat3.hh"
#include "../utility_data_structure/VecDat3.hh"
#include "../zerror_type/error_out.hh"
#include "../zfoundation/zstringstream.hh"
#include "../zparam/ZParawError.hh"
#include "ChemicalSpeciesData.hh"

class PhaseData_IO;
class ZRTSite;

ZParawError AtomSiteDataDebyeWallerFactorConvertBtoU(ZParawError BValue);
ZParawError AtomSiteDataDebyeWallerFactorConvertUtoB(ZParawError UValue);

class AtomSiteData
{
public:
	enum PositionNumber {
		PositionX,
		PositionY,
		PositionZ,
	};


	AtomSiteData();
	
	static shared_ptr<AtomSiteData> CreateWithSite(shared_ptr<ZRTSite> site);
	
	//Error check
	//You must use SetErrorCheckNecessary function right after you initialized this object.
	//The movement is not guaranteed when you use this function by the timing except it.
	ZErrorMessage SetErrorCheckNecessary(bool necessary);
	bool& IsErrorCheckNecessary() { return m_isErrorCheckNecessary; }
	
	//Label
	//You can use any ASCII characters
	ZErrorMessage SetLabel(const string& label);
	const string& GetLabel() const { return m_label; }

	//Symbol
	//He, Fe, etc...
	ZErrorMessage SetSymbol(const string& symbol);
	const string& GetSymbol() const { return m_symbol; }
	void GetNonDuplicateSymbols(vector<string>& symbols) const;
	
	const ZRTErrorType& CheckChemicalSpecies() const { return m_IsSetProperly_chemicalSpecies; }
	const vector<shared_ptr<ChemicalSpeciesData>>& GetChemicalSpecieses() const { return mChemicalSpecieses; }
	void GetNonDuplicateChemicalSpecieses(shared_ptr<vector<shared_ptr<const ChemicalSpeciesData>>>& inoutChemicalSpecieses) const;
	
	//Occupancy
	ZErrorMessage SetOccupancy(const ZParawError& occupancy);
	const ZParawError& GetOccupancy() const { return m_occupancy; }
	ZErrorMessage SetOccupancyID(const etype_ID& occupancyID);
	const etype_ID& GetOccupancyID() const { return m_occupancyID; }
	
	//Positions
	ZErrorMessage SetPositionX(const ZParawError& positionX);
	ZErrorMessage SetPositionY(const ZParawError& positionY);
	ZErrorMessage SetPositionZ(const ZParawError& positionZ);
	const ZParawError& GetPositionX() const { return m_positionX; }
	const ZParawError& GetPositionY() const { return m_positionY; }
	const ZParawError& GetPositionZ() const { return m_positionZ; }
	const ZParawError& GetPosition(PositionNumber positionNumber) const;
	const VecDat3<Double> GetPositionValues() const;
	ZErrorMessage SetDebyeWallerFactorUnit(const ZRietveldTemperatureFactorUnit& unit);
	ZErrorMessage SetDebyeWallerFactorIsAnisotropic(const bool& flag);
	ZErrorMessage SetDebyeWallerFactors(const vector<ZParawError>& DWtray);
	ZErrorMessage SetDebyeWallerFactors(const vector<ZParawError>& AnisoDWtray, const vector<ZParawError>& IsoDWtray);
	ZErrorMessage SetDebyeWallerFactorBetaIDs(const vector<etype_ID>& IDtray);
	const ZRietveldTemperatureFactorUnit& GetDebyeWallerFactorUnit() const { return m_TemperatureFactorUnit; };
	const bool& GetDebyeWallerFactorIsAnisotropic() const { return m_TemperatureFactorIsAnisotropics; };
	const vector<ZParawError>& GetDebyeWallerFactors() const { if(m_TemperatureFactorIsAnisotropics) return m_AtomTempFactorsAniso; else return m_AtomTempFactorsIso; };
	const vector<etype_ID>& GetDebyeWallerFactorBetaIDs() const { return m_AtomTempFactorBetaIDs; };

	const vector<ZParawError>& GetDebyeWallerFactorsAniso() const { return m_AtomTempFactorsAniso; };
	const vector<ZParawError>& GetDebyeWallerFactorsIso() const { return m_AtomTempFactorsIso; };

	// The argument unit must be B or U. 
	inline vector<ZParawError> GetDebyeWallerFactors(const ZRietveldTemperatureFactorUnit& unit) const;
	vector<ZParawError> GetDebyeWallerFactorsAniso(const ZRietveldTemperatureFactorUnit& unit) const;
	vector<ZParawError> GetDebyeWallerFactorsIso(const ZRietveldTemperatureFactorUnit& unit) const;

	ZErrorMessage SetPositionIDs(const VecDat3<etype_ID>& positionIDs);
	const VecDat3<etype_ID>& GetPositionIDs() const { return m_positionIDs; }
	const etype_ID& GetPositionXID() const { return m_positionIDs[0]; }
	const etype_ID& GetPositionYID() const { return m_positionIDs[1]; }
	const etype_ID& GetPositionZID() const { return m_positionIDs[2]; }
	
	//Position Multiplicity
	const Int4& GetPositionMultiplicity() const { return m_AtomPosMultiplicity; }
	void SetPositionMultiplicity(const Int4& atomPosMultiplicity, const Int4& numSiteSymmetry) { m_AtomPosMultiplicity = atomPosMultiplicity; m_NumSiteSymmetry = numSiteSymmetry; m_IsSetPositionMultiplicity = true; };
	const bool& IsSetPositionMultiplicity() const { return m_IsSetPositionMultiplicity; }
	
	//Multiplicity
	Int4 GetMultiplicity() const;
	
	// NumSiteSymmetry
	const Int4& GetNumSiteSymmetry() const { return m_NumSiteSymmetry; }
	const string GetWyckoffLetter() const;
	
	//Clear
	void Reset();
	
	ZErrorMessage checkParam() const;

	inline void setDebyeWallerFactorsBetaAniso_DefaultConstraints(const Mat_DP_constr& rhs);
	inline const Mat_DP_constr& putDebyeWallerFactorsBetaAniso_DefaultConstraints() const;
	void putEquivalentSiteData(const ZRTSpaceGroupNumber& ISG, const SymMat3<Double>& S, vector<AtomSiteData>& equiv_apos) const;

	// magnetic
	bool isMagnetic() const { return !m_labelMagnetic.empty(); }
	string putLabelMagnetic() const { return m_labelMagnetic; }
	ZParawError putSpinMagnitude() const { return m_spinMagnitude; }
	void setLabelMagnetic(const string &s) { m_labelMagnetic = s; }
	void setSpinMagnitude(const ZParawError d) { m_spinMagnitude = d; }
	ZErrorMessage setSpinMagnitudeID(const etype_ID spinMagnitudeID) { m_spinMagnitudeID = spinMagnitudeID; return ZErrorMessage(); }
	etype_ID putSpinMagnitudeID() const { return m_spinMagnitudeID; }
	void setG(const Double g0) { g = g0; }
	Double putG() const { return g; }
	void set_atomLabel_ofNuclearPhase(const string &label) { m_atomLabel_ofNuclearPhase = label; }
	string get_atomLabel_ofNuclearPhase() const { return m_atomLabel_ofNuclearPhase; }
	void set_tempFactorMultiplier(const vector<Vec_DP> &coeffs) { m_tempFactorMultiplier = coeffs; }
	const vector<Vec_DP> &get_tempFactorMultiplier() const { return m_tempFactorMultiplier; }
	void set_xyzMultiplier(const VecDat3<VecDat3<Double> > &coeffs) { m_xyzMultiplier = coeffs; }
	const VecDat3<VecDat3<Double> > &get_xyzMultiplier() const { return m_xyzMultiplier; }
	
	void SetPhaseData_IO(PhaseData_IO* phase);
	
	void DebugDescription(zstringstream& stream) const;
	void DebugDescriptionCollinear(zstringstream& stream) const;
	void DebugDescriptionPropagationVector(zstringstream& stream) const;
		
protected:
	PhaseData_IO* m_phase;
	bool m_isErrorCheckNecessary;
	string m_label;
	string m_symbol;
	ZParawError m_occupancy;
	ZParawError m_positionX; // Position.
	ZParawError m_positionY; // Position.
	ZParawError m_positionZ; // Position.
	bool m_TemperatureFactorIsAnisotropics;	// Anisotropic/Isotropic.
	ZRietveldTemperatureFactorUnit m_TemperatureFactorUnit;	// B/U/beta.
	vector<ZParawError> m_AtomTempFactorsAniso;	// An array of size 6 for the Debye-Waller factor. B/U/beta, anisotropic.
	vector<ZParawError> m_AtomTempFactorsIso;	// An array of size 1 for the Debye-Waller factor. B/U/beta, isotropic.
	
	bool m_IsSetTempFactors_DefaultConstraints;
	Mat_DP_constr m_AtomTempFactorsBetaAniso_DefaultConstraints;	// An array of size 6 for the Debye-Waller factor beta.

	// Data on atoms.
	ZRTErrorType m_IsSetProperly_chemicalSpecies;
	vector<shared_ptr<ChemicalSpeciesData>> mChemicalSpecieses;
	
	etype_ID m_occupancyID;
	VecDat3<etype_ID> m_positionIDs;
	vector<etype_ID> m_AtomTempFactorBetaIDs;
	
	bool m_IsSetPositionMultiplicity;
	Int4 m_NumSiteSymmetry;
	Int4 m_AtomPosMultiplicity;

	// magnetic
	string m_labelMagnetic; // e.g., "Fe3+"
	ZParawError m_spinMagnitude; // can be negative (used in the collinear case)
	etype_ID m_spinMagnitudeID; //  (used in the collinear case)
	Double g;
	string m_atomLabel_ofNuclearPhase; // atom label of the nuclear phase definition corresponding to the magnetic atom. e.g., "Mn1"
	VecDat3<VecDat3<Double> > m_xyzMultiplier; // 3x3. d(x_mag) = d(x_nuc) x [0][0] + d(y_nuc) x [0][1] + d(z_nuc) x [0][2]
	vector<Vec_DP> m_tempFactorMultiplier; // 6x6. d(beta11_mag) = d(beta11_nuc) x [0][0] + d(beta22_nuc) x [0][1] + ...
};


inline vector<ZParawError> AtomSiteData::GetDebyeWallerFactors(const ZRietveldTemperatureFactorUnit& unit) const
{
	if( m_TemperatureFactorIsAnisotropics )
	{
		return GetDebyeWallerFactorsAniso(unit);
	}
	else
	{
		return GetDebyeWallerFactorsIso(unit);
	}
}

inline void AtomSiteData::setDebyeWallerFactorsBetaAniso_DefaultConstraints(const Mat_DP_constr& rhs)
{ 
	m_AtomTempFactorsBetaAniso_DefaultConstraints = rhs;
	m_IsSetTempFactors_DefaultConstraints = true;
};

inline const Mat_DP_constr& AtomSiteData::putDebyeWallerFactorsBetaAniso_DefaultConstraints() const
{
	assert( m_IsSetTempFactors_DefaultConstraints );
	return m_AtomTempFactorsBetaAniso_DefaultConstraints;
}
