/*
 AtomSiteData.cc
 
 Author: HIRAI Hiroaki, R. Tomiyasu
 
 Copyright 2010-2025 Project Z-Code. All rights reserved.
 */

#include "../ZRTSite_.h"

#include "AtomSiteData.hh"
#include "ChemicalSpeciesData.hh"
#include "DebyeWaller_util.hh"
#include "../utility_atomic_data/atomic_data.hh"
#include "../utility_atomic_data/scattering_factor.hh"
#include "../space_group/NormalSeriesTray.hh"
#include "../space_group/Wyckoff_Seed.hh"
#include "../PhaseData_IO.hh"
#include "../ZRTSite.hh"
#include "../ZRTValueString.hh"

ZParawError AtomSiteDataDebyeWallerFactorConvertBtoU(ZParawError BValue)
{
	static const Double coef = 1.0/(8.0*M_PI*M_PI); 
	ZParawError parawError(BValue.value*coef, BValue.error*coef);
	return parawError;
}

ZParawError AtomSiteDataDebyeWallerFactorConvertUtoB(ZParawError UValue)
{
	static const Double coef = 8.0*M_PI*M_PI; 
	ZParawError parawError(UValue.value*coef, UValue.error*coef);
	return parawError;
}

AtomSiteData::AtomSiteData()
{
	m_isErrorCheckNecessary = true;
	
	Reset();
}

shared_ptr<AtomSiteData> AtomSiteData::CreateWithSite(shared_ptr<ZRTSite> site)
{
	shared_ptr<AtomSiteData> atomData = make_shared<AtomSiteData>();
	atomData->SetLabel(site->GetLabel());
	
	atomData->SetSymbol(site->GetSymbolForAnalyze());
	
	atomData->SetOccupancy(site->GetOccupancy());
	
	atomData->SetPositionX(site->GetPositionX());
	atomData->SetPositionY(site->GetPositionY());
	atomData->SetPositionZ(site->GetPositionZ());
	
	atomData->SetOccupancyID((const etype_ID)site->GetOccupancy()->GetCorrectedID());
	
	VecDat3<etype_ID> positionsID((const etype_ID)site->GetPositionX()->GetCorrectedID(),
								  (const etype_ID)site->GetPositionY()->GetCorrectedID(),
								  (const etype_ID)site->GetPositionZ()->GetCorrectedID()
								  );
	atomData->SetPositionIDs(positionsID);
	
	bool isAnisotropic = ZRTSiteIsAtomicDisplacementAnisotropicType(site->GetAtomicDisplacementIsotropicType());
	
	vector<ZParawError> atomicDisplacements;
	vector<etype_ID> atomicDisplacementsIDs;
	if(isAnisotropic){
		atomicDisplacements.push_back(site->GetAtomicDisplacement11());
		atomicDisplacements.push_back(site->GetAtomicDisplacement22());
		atomicDisplacements.push_back(site->GetAtomicDisplacement33());
		atomicDisplacements.push_back(site->GetAtomicDisplacement12());
		atomicDisplacements.push_back(site->GetAtomicDisplacement13());
		atomicDisplacements.push_back(site->GetAtomicDisplacement23());
		
		atomicDisplacementsIDs.push_back((const etype_ID)site->GetAtomicDisplacement11()->GetCorrectedID());
		atomicDisplacementsIDs.push_back((const etype_ID)site->GetAtomicDisplacement22()->GetCorrectedID());
		atomicDisplacementsIDs.push_back((const etype_ID)site->GetAtomicDisplacement33()->GetCorrectedID());
		atomicDisplacementsIDs.push_back((const etype_ID)site->GetAtomicDisplacement12()->GetCorrectedID());
		atomicDisplacementsIDs.push_back((const etype_ID)site->GetAtomicDisplacement13()->GetCorrectedID());
		atomicDisplacementsIDs.push_back((const etype_ID)site->GetAtomicDisplacement23()->GetCorrectedID());
	}else{
		atomicDisplacements.push_back(site->GetAtomicDisplacementIso());
		
		atomicDisplacementsIDs.push_back((const etype_ID)site->GetAtomicDisplacementIso()->GetCorrectedID());
	}
	atomData->SetDebyeWallerFactorIsAnisotropic(isAnisotropic);
	atomData->SetDebyeWallerFactors(atomicDisplacements);
	atomData->SetDebyeWallerFactorBetaIDs(atomicDisplacementsIDs);
	atomData->SetDebyeWallerFactorUnit((ZRietveldTemperatureFactorUnit)site->GetAtomicDisplacementUnit());

	return atomData;
}

void AtomSiteData::Reset()
{
	m_phase = NULL;
	
	m_label = "";
	m_symbol = "";
	m_occupancy.value = 1.0;
	m_occupancy.error = 0.0;
	m_occupancyID = _ZRietveldIDFixed;
	m_positionX.value = 0.0;
	m_positionX.error = 0.0;
	m_positionY.value = 0.0;
	m_positionY.error = 0.0;
	m_positionZ.value = 0.0;
	m_positionZ.error = 0.0;
	m_positionIDs[0] = _ZRietveldIDFixed;
	m_positionIDs[1] = _ZRietveldIDFixed;
	m_positionIDs[2] = _ZRietveldIDFixed;
	m_TemperatureFactorIsAnisotropics = false;
	m_AtomTempFactorsAniso.resize(ZRTAtomicDisplacementAnisoSize, 0.0);	// Debye-Waller factor. B/U/beta, isotropic or anisotropic.
	m_AtomTempFactorsIso.resize(ZRTAtomicDisplacementIsoSize, 0.0);	// Debye-Waller factor. B/U/beta, isotropic or anisotropic.
	m_AtomTempFactorBetaIDs.resize(ZRTAtomicDisplacementIsoSize, _ZRietveldIDFixed); 		// Each atom.

	m_labelMagnetic = "";
	m_spinMagnitude.error = 0.0;
	m_spinMagnitude.value = 0.0;
	g = 2.0;
	m_spinMagnitudeID = _ZRietveldIDFixed;

	m_AtomTempFactorsBetaAniso_DefaultConstraints.resize(ZRTAtomicDisplacementAnisoSize);
	for(Int4 i=0; i<ZRTAtomicDisplacementAnisoSize; i++) m_AtomTempFactorsBetaAniso_DefaultConstraints[i].ID = _ZRietveldIDFixed;	// All fixed.

	m_IsSetProperly_chemicalSpecies = ZRTErrorTypeFailedSetAtomSymbolEmptyCharacter;
	m_NumSiteSymmetry = 0;
	m_AtomPosMultiplicity = 0;
	m_IsSetPositionMultiplicity = false;
	m_IsSetTempFactors_DefaultConstraints = false;
}


ZErrorMessage AtomSiteData::checkParam() const
{
	ZErrorMessage error;
	
	error = ZErrorMessage(ZRTSiteCheckLabel(this->GetLabel().c_str()), __FILE__, __LINE__, __FUNCTION__);
	if(error.putErrorType() != ZRTErrorTypeNoError)
	{
		return error;
	}	

	if( this->GetSymbol().empty() )
	{
		return nerror(ZRTErrorTypeFailedSetAtomSymbolEmptyCharacter, __FILE__, __LINE__, __FUNCTION__);
	}
	else
	{
		if( m_IsSetProperly_chemicalSpecies != ZRTErrorTypeNoError ) return ZErrorMessage(m_IsSetProperly_chemicalSpecies, __FILE__, __LINE__, __FUNCTION__);
	}
	
	error = ZErrorMessage(ZRTSiteCheckOccupancy(this->GetOccupancy().value), __FILE__, __LINE__, __FUNCTION__);
	if(error.putErrorType() != ZRTErrorTypeNoError)
	{
		return error;
	}
	
	error = ZErrorMessage(ZRTSiteCheckOccupancyID((ZRTID)this->GetOccupancyID()), __FILE__, __LINE__, __FUNCTION__);
	if(error.putErrorType() != ZRTErrorTypeNoError)
	{
		return error;
	}
	
	error = ZErrorMessage(ZRTSiteCheckPositionX(this->GetPositionX().value), __FILE__, __LINE__, __FUNCTION__);
	if(error.putErrorType() != ZRTErrorTypeNoError){
		return error;
	}

	error = ZErrorMessage(ZRTSiteCheckPositionY(this->GetPositionY().value), __FILE__, __LINE__, __FUNCTION__);
	if(error.putErrorType() != ZRTErrorTypeNoError)
	{
		return error;
	}

	error = ZErrorMessage(ZRTSiteCheckPositionZ(this->GetPositionZ().value), __FILE__, __LINE__, __FUNCTION__);
	if(error.putErrorType() != ZRTErrorTypeNoError)
	{
		return error;
	}

	error = ZErrorMessage(ZRTSiteCheckPositionXID((ZRTID)this->GetPositionXID()), __FILE__, __LINE__, __FUNCTION__);
	if(error.putErrorType() != ZRTErrorTypeNoError)
	{
		return error;
	}
	
	error = ZErrorMessage(ZRTSiteCheckPositionYID((ZRTID)this->GetPositionYID()), __FILE__, __LINE__, __FUNCTION__);
	if(error.putErrorType() != ZRTErrorTypeNoError)
	{
		return error;
	}

	error = ZErrorMessage(ZRTSiteCheckPositionZID((ZRTID)this->GetPositionZID()), __FILE__, __LINE__, __FUNCTION__);
	if(error.putErrorType() != ZRTErrorTypeNoError)
	{
		return error;
	}

	for(UInt4 i=0; i<m_AtomTempFactorBetaIDs.size(); i++)
	{
		error = ZErrorMessage(ZRTSiteCheckAtomicDisplacementID((ZRTID)m_AtomTempFactorBetaIDs[i]), __FILE__, __LINE__, __FUNCTION__);
		if(error.putErrorType() != ZRTErrorTypeNoError)
		{
			return error;
		}
	}
	
	return error;
}


ZErrorMessage AtomSiteData::SetLabel(const string& label)
{
	m_label = label;
	
	ZErrorMessage error = ZErrorMessage(ZRTSiteCheckLabel(label.c_str()), __FILE__, __LINE__, __FUNCTION__);
	if(error.putErrorType() != ZRTErrorTypeNoError)
	{
		return error;
	}
	
	return ZErrorMessage();
}

static ZRTErrorType put_name_neutron(const string& inputSymbol, string& outputSymbolNeutron)
{
	string atom_name = inputSymbol;
	
	if( atom_name == "D" )
	{
		outputSymbolNeutron = "2H";
		return ZRTErrorTypeNoError;
	}
	else if( atom_name == "T" )
	{
		outputSymbolNeutron = "3H";
		return ZRTErrorTypeNoError;
	}
	
	// Set m_atom_name_inp.
	Int4 str_len = (Int4)atom_name.length();
	
	// Set AtomicSymbol.
	if( str_len > 3 && atom_name.substr( str_len-3, str_len ) == "val" )
	{
		outputSymbolNeutron = atom_name.substr( 0, str_len-3 );	// "*val" => "*"
	}
	else if( atom_name.at( str_len-1 ) == '+' || atom_name.at( str_len-1 ) == '-' )
	{
		if( str_len < 2 )
		{
			return ZRTErrorTypeFailedSetAtomSymbolNeutron;
		}
		else if( isdigit( atom_name.at( str_len-2 ) ) ) outputSymbolNeutron = atom_name.substr( 0, str_len-2 );	// "*4+" => "*", 	// "*4-" => "*"
		else outputSymbolNeutron = atom_name.substr( 0, str_len-1 );	// "*+" => "*", 	// "*-" => "*"
	}
	else {
		outputSymbolNeutron = atom_name;
	}
	
	return ZRTErrorTypeNoError;
}

ZErrorMessage AtomSiteData::SetErrorCheckNecessary(bool necessary)
{
	if( !m_isErrorCheckNecessary && necessary )
	{
		m_isErrorCheckNecessary = necessary;
		return checkParam();
	}
	else
	{
		m_isErrorCheckNecessary = necessary;
	}
	return ZErrorMessage();
}

ZErrorMessage AtomSiteData::SetDebyeWallerFactorUnit(const ZRietveldTemperatureFactorUnit& unit)
{
	assert( unit == kZRietveldTemperatureFactorUnitB
		   ||  unit == kZRietveldTemperatureFactorUnitU
		   ||  unit == kZRietveldTemperatureFactorUnitBeta );
	
	m_TemperatureFactorUnit = unit;
	return ZErrorMessage();
}

static ZRTErrorType put_name_Xray(const string& inputSymbol, string& outputSymbolXray)
{
	string atom_name = inputSymbol;
	
	if( atom_name.at(0) == 'D' || atom_name.at(0) == 'T' )
	{
		if( atom_name.length() < 2 || !isalpha( atom_name.at(1) ) ) atom_name.at(0) = 'H';
	}
	
	Int4 str_len0 = 0;
	while( isdigit( atom_name.at( str_len0 ) ) ) str_len0++;
	
	Int4 str_len = (Int4)atom_name.length();
	
	outputSymbolXray = atom_name.substr( str_len0, str_len );
	if( atom_name.at(str_len-1) == '+' || atom_name.at(str_len-1) == '-' )
	{
		if( outputSymbolXray.length() < 2 )
		{
			return ZRTErrorTypeFailedSetAtomSymbolXray;
		}
		if( !isdigit( atom_name.at(str_len-2) ) ) outputSymbolXray.insert(str_len-1, 1, '1');	// Insert '1' behind '+' or '-'.
	}
	
	return ZRTErrorTypeNoError;
}


ZErrorMessage AtomSiteData::SetSymbol(const string& symbol)
{
	m_IsSetProperly_chemicalSpecies = ZRTErrorTypeNoError;
	mChemicalSpecieses.clear();

	m_symbol = remove_blank(symbol);
	if( symbol.empty() )
	{
		m_IsSetProperly_chemicalSpecies = ZRTErrorTypeFailedSetAtomSymbolEmptyCharacter;
		return nerror(ZRTErrorTypeFailedSetAtomSymbolEmptyCharacter, __FILE__, __LINE__, __FUNCTION__);
	}

	vector<string> stray;
	string s;
	istringstream iss(symbol);
	ZErrorMessage zerr;
	do{
		zerr = getdelim(iss, s, ":");
		stray.push_back(s);
	} while ( zerr.putErrorType() == ZRTErrorTypeNoError );

	const size_t isize = stray.size();
	for(size_t i=0; i<isize; i++){
		mChemicalSpecieses.push_back(make_shared<ChemicalSpeciesData>());
	}

	string atom_name;
	ZRTErrorType zerr_type;
	if( isize < (size_t)2 )
	{
		mChemicalSpecieses[0]->SetSymbol(GetSymbol());
		m_IsSetProperly_chemicalSpecies = put_name_Xray( stray[0], mChemicalSpecieses[0]->m_symbolXray );
		zerr_type = put_name_neutron( stray[0], atom_name );
		if( zerr_type != ZRTErrorTypeNoError ) m_IsSetProperly_chemicalSpecies = zerr_type;
		else
		{
			m_IsSetProperly_chemicalSpecies = IntAtomicSymbol( atom_name, mChemicalSpecieses[0]->m_symbolNeutron );
		}
	}
	else
	{
		vector<string> stray2(isize);
		for(size_t i=0; i<isize; i++)
		{
			istringstream iss2(stray[i]);
			zerr = getdelim(iss2, stray2[i], "(");
			if( ZRTErrorTypeNoError != zerr.putErrorType() )
			{
				m_IsSetProperly_chemicalSpecies = ZRTErrorTypeFailedSetChemicalSpecies;
				return zerr;
			}

			zerr = getdelim(iss2, s, ")");
			if( ZRTErrorTypeNoError != zerr.putErrorType() )
			{
				m_IsSetProperly_chemicalSpecies = ZRTErrorTypeFailedSetChemicalSpecies;
				return zerr;
			}
			Double ratio = 0;
			if( !str2num(s, ratio) )
			{
				m_IsSetProperly_chemicalSpecies = ZRTErrorTypeFailedSetChemicalSpecies;
				return nerror(ZRTErrorTypeFailedSetChemicalSpecies, "Some occupancies of chemical species are not a number", __FILE__, __LINE__, __FUNCTION__);
			}else{
				mChemicalSpecieses[i]->SetRatio(ratio);
			}
		}

		Double ratio_sum = 0.0;
		for(size_t i=0; i<isize; i++)
		{
			mChemicalSpecieses[i]->SetSymbol(stray2[i]);

			zerr_type = put_name_Xray( stray2[i], mChemicalSpecieses[i]->m_symbolXray );
			if( zerr_type != ZRTErrorTypeNoError ){
				m_IsSetProperly_chemicalSpecies = zerr_type;
			}

			zerr_type = put_name_neutron( stray2[i], atom_name );
			if( zerr_type != ZRTErrorTypeNoError ){
				m_IsSetProperly_chemicalSpecies = zerr_type;
			}else{
				zerr_type = IntAtomicSymbol( atom_name, mChemicalSpecieses[i]->m_symbolNeutron );
				if( zerr_type != ZRTErrorTypeNoError ) m_IsSetProperly_chemicalSpecies = zerr_type;
			}

			if( mChemicalSpecieses[i]->GetRatio() <= 0.0 ){
				m_IsSetProperly_chemicalSpecies = ZRTErrorTypeOccupancyVirtualChemicalSpeciesUnderZero;
			}
			ratio_sum += mChemicalSpecieses[i]->GetRatio();
		}

		if( ratio_sum <= 0.0 ){
			m_IsSetProperly_chemicalSpecies = ZRTErrorTypeTotalOccupancyVirtualChemicalSpeciesIsMinus;
		}
	}
	
	return ZErrorMessage();
}

void AtomSiteData::GetNonDuplicateSymbols(vector<string>& symbols) const
{
//	for(const ChemicalSpeciesData& chemicalSpecies : m_chemicalSpecies){
//		for(string registeredSymbol : symbols){
//			if(registeredSymbol == chemicalSpecies)
//		}
//	}
}

void AtomSiteData::GetNonDuplicateChemicalSpecieses(shared_ptr<vector<shared_ptr<const ChemicalSpeciesData>>>& inoutChemicalSpecieses) const
{
	for(const shared_ptr<ChemicalSpeciesData>& chemicalSpecies : mChemicalSpecieses){
		bool isNonDuplicate = true;
		for(const shared_ptr<const ChemicalSpeciesData>& nonDuplicateCehmicalSpecies : *inoutChemicalSpecieses){
			if(nonDuplicateCehmicalSpecies->GetSymbol() == chemicalSpecies->GetSymbol()){
				isNonDuplicate = false;
				break;
			}
		}
		if(isNonDuplicate){
			inoutChemicalSpecieses->push_back(chemicalSpecies);
		}
	}
}

ZErrorMessage AtomSiteData::SetOccupancy(const ZParawError& occupancy)
{
	m_occupancy = occupancy;

	ZErrorMessage error = ZErrorMessage(ZRTSiteCheckOccupancy(occupancy.value), __FILE__, __LINE__, __FUNCTION__);
	if(error.putErrorType() != ZRTErrorTypeNoError)
	{
		return error;
	}
	
	return ZErrorMessage();
}

ZErrorMessage AtomSiteData::SetOccupancyID(const etype_ID& occupancyID)
{
	m_occupancyID = occupancyID;

	ZErrorMessage error = ZErrorMessage(ZRTSiteCheckOccupancyID((ZRTID)occupancyID), __FILE__, __LINE__, __FUNCTION__);
	if(error.putErrorType() != ZRTErrorTypeNoError)
	{
		return error;
	}

	return ZErrorMessage();
}

ZErrorMessage AtomSiteData::SetPositionX(const ZParawError& positionX)
{
	m_positionX = positionX;
	m_IsSetPositionMultiplicity = false;
	m_IsSetTempFactors_DefaultConstraints = false;

	ZErrorMessage error = ZErrorMessage(ZRTSiteCheckPositionX(positionX.value), __FILE__, __LINE__, __FUNCTION__);
	if(error.putErrorType() != ZRTErrorTypeNoError){
		return error;
	}
	return ZErrorMessage();
}

ZErrorMessage AtomSiteData::SetPositionY(const ZParawError& positionY)
{
	m_positionY = positionY;
	m_IsSetPositionMultiplicity = false;
	m_IsSetTempFactors_DefaultConstraints = false;

	ZErrorMessage error = ZErrorMessage(ZRTSiteCheckPositionY(positionY.value), __FILE__, __LINE__, __FUNCTION__);
	if(error.putErrorType() != ZRTErrorTypeNoError){
		return error;
	}

	return ZErrorMessage();
}

ZErrorMessage AtomSiteData::SetPositionZ(const ZParawError& positionZ)
{
	m_positionZ = positionZ;
	m_IsSetPositionMultiplicity = false;
	m_IsSetTempFactors_DefaultConstraints = false;

	ZErrorMessage error = ZErrorMessage(ZRTSiteCheckPositionZ(positionZ.value), __FILE__, __LINE__, __FUNCTION__);
	if(error.putErrorType() != ZRTErrorTypeNoError){
		return error;
	}
	return ZErrorMessage();
}


ZErrorMessage AtomSiteData::SetPositionIDs(const VecDat3<etype_ID>& positionIDs)
{
	m_positionIDs = positionIDs;
	m_IsSetPositionMultiplicity = false;

	ZErrorMessage error = ZErrorMessage(ZRTSiteCheckPositionXID((ZRTID)positionIDs[0]), __FILE__, __LINE__, __FUNCTION__);
	if(error.putErrorType() != ZRTErrorTypeNoError){
		return error;
	}
	error = ZErrorMessage(ZRTSiteCheckPositionYID((ZRTID)positionIDs[1]), __FILE__, __LINE__, __FUNCTION__);
	if(error.putErrorType() != ZRTErrorTypeNoError){
		return error;
	}
	error = ZErrorMessage(ZRTSiteCheckPositionZID((ZRTID)positionIDs[2]), __FILE__, __LINE__, __FUNCTION__);
	if(error.putErrorType() != ZRTErrorTypeNoError){
		return error;
	}

	return ZErrorMessage();
}




const ZParawError& AtomSiteData::GetPosition(PositionNumber positionNumber) const
{
	switch (positionNumber) {
		case PositionX:{
			return m_positionX;
		}break;
		case PositionY:{
			return m_positionY;
		}break;
		case PositionZ:{
			return m_positionZ;
		}break;
		default:{
			assert( false );
			break;
		}
	}
	return m_positionX;
}

const VecDat3<Double> AtomSiteData::GetPositionValues() const
{
	return VecDat3<Double>(m_positionX.value, m_positionY.value, m_positionZ.value);
}


ZErrorMessage AtomSiteData::SetDebyeWallerFactorIsAnisotropic(const bool& flag)
{
	m_TemperatureFactorIsAnisotropics = flag;
	m_AtomTempFactorBetaIDs.resize( PNTempFactor(flag) );
	
	return ZErrorMessage();
}


ZErrorMessage AtomSiteData::SetDebyeWallerFactors(const vector<ZParawError>& DWtray)
{
	if( m_TemperatureFactorIsAnisotropics )
	{
		return SetDebyeWallerFactors(DWtray, vector<ZParawError>(ZRTAtomicDisplacementIsoSize,0.0));
	}
	else
	{
		return SetDebyeWallerFactors(vector<ZParawError>(ZRTAtomicDisplacementAnisoSize,0.0), DWtray);
	}
}


ZErrorMessage AtomSiteData::SetDebyeWallerFactors(const vector<ZParawError>& AnisoDWtray, const vector<ZParawError>& IsoDWtray)
{
	m_AtomTempFactorsAniso = AnisoDWtray;
	m_AtomTempFactorsIso = IsoDWtray;

	if( (Int4)AnisoDWtray.size()!= ZRTAtomicDisplacementAnisoSize || (Int4)IsoDWtray.size()!= ZRTAtomicDisplacementIsoSize )
	{
		return nerror(ZRTErrorTypeArgmentSize, __FILE__, __LINE__, __FUNCTION__);
	}

	return ZErrorMessage();
}


ZErrorMessage AtomSiteData::SetDebyeWallerFactorBetaIDs(const vector<etype_ID>& IDtray)
{
	m_AtomTempFactorBetaIDs = IDtray;

	if( ( !m_TemperatureFactorIsAnisotropics && (Int4)IDtray.size()!=ZRTAtomicDisplacementIsoSize )
				|| ( m_TemperatureFactorIsAnisotropics && (Int4)IDtray.size()!=ZRTAtomicDisplacementAnisoSize ) )
	{
		return nerror(ZRTErrorTypeArgmentSize, __FILE__, __LINE__, __FUNCTION__);
	}

	ZErrorMessage error;
	for(UInt4 i=0; i<m_AtomTempFactorBetaIDs.size(); i++)
	{
		error = ZErrorMessage(ZRTSiteCheckAtomicDisplacementID((ZRTID)m_AtomTempFactorBetaIDs[i]), __FILE__, __LINE__, __FUNCTION__);
		if(error.putErrorType() != ZRTErrorTypeNoError)
		{
			return error;
		}
	}

	return ZErrorMessage();
}


vector<ZParawError> AtomSiteData::GetDebyeWallerFactorsAniso(const ZRietveldTemperatureFactorUnit& unit) const
{
	assert( unit == kZRietveldTemperatureFactorUnitB
				||  unit == kZRietveldTemperatureFactorUnitU
				||  unit == kZRietveldTemperatureFactorUnitBeta );
	
	vector<ZParawError> temperatureFactor;
	switch (unit) {
		case kZRietveldTemperatureFactorUnitB:{
			switch(m_TemperatureFactorUnit){
				case kZRietveldTemperatureFactorUnitB:{
					return m_AtomTempFactorsAniso;
				}break;
				case kZRietveldTemperatureFactorUnitU:{
					const Int4 pn = (Int4)m_AtomTempFactorsAniso.size();
					temperatureFactor.resize(pn);
					for(Int4 j=0; j<pn; j++)
					{
						temperatureFactor[j].value = ZRTSiteAtomicDisplacementConvertBFromU(m_AtomTempFactorsAniso[j].value);
						temperatureFactor[j].error = ZRTSiteAtomicDisplacementConvertBFromU(m_AtomTempFactorsAniso[j].error);
					}
				}
				case kZRietveldTemperatureFactorUnitBeta:{
					//Not Implemented
				}break;
//				case kZRietveldTemperatureFactorUnitUndefined:
				default:{
				}break;
			}
		}break;
		case kZRietveldTemperatureFactorUnitU:{
			switch(m_TemperatureFactorUnit){
				case kZRietveldTemperatureFactorUnitB:{
					const Int4 pn = (Int4)m_AtomTempFactorsAniso.size();
					temperatureFactor.resize(pn);
					for(Int4 j=0; j<pn; j++)
					{
						temperatureFactor[j].value = ZRTSiteAtomicDisplacementConvertUFromB(m_AtomTempFactorsAniso[j].value);
						temperatureFactor[j].error = ZRTSiteAtomicDisplacementConvertUFromB(m_AtomTempFactorsAniso[j].error);
					}
				}break;
				case kZRietveldTemperatureFactorUnitU:{
					return m_AtomTempFactorsAniso;
				}break;
				case kZRietveldTemperatureFactorUnitBeta:{
					//Not Implemented
				}break;
//				case kZRietveldTemperatureFactorUnitUndefined:
				default:{
				}break;
			}
		}break;
		case kZRietveldTemperatureFactorUnitBeta:{
			switch(m_TemperatureFactorUnit){
				case kZRietveldTemperatureFactorUnitB:{
					//Not Implemented
				}break;
				case kZRietveldTemperatureFactorUnitU:{
					//Not Implemented
				}break;
				case kZRietveldTemperatureFactorUnitBeta:{
					//Not Implemented
				}break;
//				case kZRietveldTemperatureFactorUnitUndefined:
				default:{
				}break;
			}
		}break;
//		case kZRietveldTemperatureFactorUnitUndefined:
		default:{
			//
		}break;
	}
	return temperatureFactor;
}


vector<ZParawError> AtomSiteData::GetDebyeWallerFactorsIso(const ZRietveldTemperatureFactorUnit& unit) const
{
	assert( m_TemperatureFactorUnit == kZRietveldTemperatureFactorUnitB
				||  m_TemperatureFactorUnit == kZRietveldTemperatureFactorUnitU );
	assert( unit == kZRietveldTemperatureFactorUnitB
				||  unit == kZRietveldTemperatureFactorUnitU );
	
	vector<ZParawError> temperatureFactor;
	switch (unit) {
		case kZRietveldTemperatureFactorUnitB:{
			switch(m_TemperatureFactorUnit){
				case kZRietveldTemperatureFactorUnitB:{
					return m_AtomTempFactorsIso;
				}break;
				case kZRietveldTemperatureFactorUnitU:{
					const Int4 pn = (Int4)m_AtomTempFactorsIso.size();
					temperatureFactor.resize(pn);
					for(Int4 j=0; j<pn; j++)
					{
						temperatureFactor[j].value = ZRTSiteAtomicDisplacementConvertBFromU(m_AtomTempFactorsIso[j].value);
						temperatureFactor[j].error = ZRTSiteAtomicDisplacementConvertBFromU(m_AtomTempFactorsIso[j].error);
					}
				}
				case kZRietveldTemperatureFactorUnitBeta:{
					//Not Implemented
				}break;
//				case kZRietveldTemperatureFactorUnitUndefined:
				default:{
				}break;
			}
		}break;
		case kZRietveldTemperatureFactorUnitU:{
			switch(m_TemperatureFactorUnit){
				case kZRietveldTemperatureFactorUnitB:{
					const Int4 pn = (Int4)m_AtomTempFactorsIso.size();
					temperatureFactor.resize(pn);
					for(Int4 j=0; j<pn; j++)
					{
						temperatureFactor[j].value = ZRTSiteAtomicDisplacementConvertUFromB(m_AtomTempFactorsIso[j].value);
						temperatureFactor[j].error = ZRTSiteAtomicDisplacementConvertUFromB(m_AtomTempFactorsIso[j].error);
					}
				}break;
				case kZRietveldTemperatureFactorUnitU:{
					return m_AtomTempFactorsIso;
				}break;
				case kZRietveldTemperatureFactorUnitBeta:{
					//Not Implemented
				}break;
//				case kZRietveldTemperatureFactorUnitUndefined:
				default:{
				}break;
			}
		}break;
		case kZRietveldTemperatureFactorUnitBeta:{
			switch(m_TemperatureFactorUnit){
				case kZRietveldTemperatureFactorUnitB:{
					//Not Implemented
				}break;
				case kZRietveldTemperatureFactorUnitU:{
					//Not Implemented
				}break;
				case kZRietveldTemperatureFactorUnitBeta:{
					//Not Implemented
				}break;
//				case kZRietveldTemperatureFactorUnitUndefined:
				default:{
				}break;
			}
		}break;
//		case kZRietveldTemperatureFactorUnitUndefined:
		default:{
			//
		}break;
	}
	return temperatureFactor;
}

Int4 AtomSiteData::GetMultiplicity() const {
	VecDat3<Double> atomPosition=0.0;
	atomPosition[0] = GetPositionX().value;
	atomPosition[1] = GetPositionY().value;
	atomPosition[2] = GetPositionZ().value;
	
	
	NormalSeriesTray normalSeriesSpaceGroup(m_phase->putSpaceGroup());
	vector< XYZCoord<StringS1> > coset_rep;
	vector<SymmetricOperation> gen_stabilizer;
	vector< XYZCoord<S1> > atomPositionEquiv;
	
	normalSeriesSpaceGroup.putSiteSymmetry(atomPosition, atomPositionEquiv, coset_rep, gen_stabilizer);
	
	return (int)atomPositionEquiv.size();
}

const string AtomSiteData::GetWyckoffLetter() const
{
	return Wyckoff_Seed::Find(m_phase->putSpaceGroup(), GetPositionX().value, GetPositionY().value, GetPositionZ().value).WYCKOFF_LETTER;
}

void AtomSiteData::putEquivalentSiteData(const ZRTSpaceGroupNumber& ISG,
		const SymMat3<Double>& S, vector<AtomSiteData>& equiv_apos) const
{
	NormalSeriesTray norgp(ISG);
	vector< XYZCoord<S1> > apos_equiv;
	vector< XYZCoord<StringS1> > coset_rep;

	// Obtain equivalent atomic positions.
	vector<SymmetricOperation> gen_stabilizer;
	const Int4 num_site_symmetry = norgp.putSiteSymmetry(VecDat3<Double>(m_positionX.value, m_positionY.value, m_positionZ.value),
							apos_equiv, coset_rep, gen_stabilizer);
	const Int4 anum = (Int4)apos_equiv.size();
	equiv_apos.resize(anum, *this);

	// Debye-Waller factor (beta)
	vector<ZParawError> temp_factor(ZRTAtomicDisplacementAnisoSize);
	temp_factor[0] = m_AtomTempFactorsAniso[0].value;
	temp_factor[1] = m_AtomTempFactorsAniso[1].value;
	temp_factor[2] = m_AtomTempFactorsAniso[2].value;
	temp_factor[3] = m_AtomTempFactorsAniso[3].value;
	temp_factor[4] = m_AtomTempFactorsAniso[4].value;
	temp_factor[5] = m_AtomTempFactorsAniso[5].value;

	vector<ZParawError> temp_factor_beta(ZRTAtomicDisplacementAnisoSize);
	if( m_TemperatureFactorUnit == kZRietveldTemperatureFactorUnitBeta )
	{
		temp_factor_beta = temp_factor;
	}
	else
	{
		transformTempFactorToBeta(m_TemperatureFactorUnit, S, true, temp_factor, temp_factor_beta);
	}

	SymMat3<Double> new_temp_factor_beta_sym;
	SymMat<Double> S_covar(6,0.0);
	vector<ZParawError> new_temp_factor_beta(ZRTAtomicDisplacementAnisoSize);

	vector< XYZCoord<S1> >::const_iterator it=apos_equiv.begin();
	NRMat<Double> coef_mat(3,3);
	for(Int4 i=0; i<anum; i++, it++)
	{
		equiv_apos[i].m_positionX = (*it)[0].toDouble();
		equiv_apos[i].m_positionY = (*it)[1].toDouble();
		equiv_apos[i].m_positionZ = (*it)[2].toDouble();
		equiv_apos[i].SetPositionMultiplicity(anum, num_site_symmetry);

		putMatrixForm(coset_rep[i], coef_mat);
		for(Int4 j=0; j<3; j++)
		{
			for(Int4 k=j; k<3; k++)
			{
				new_temp_factor_beta_sym(j,k) = temp_factor_beta[0].value*coef_mat[j][0]*coef_mat[k][0]
						+ temp_factor_beta[1].value*coef_mat[j][1]*coef_mat[k][1]
						+ temp_factor_beta[2].value*coef_mat[j][2]*coef_mat[k][2]
						+ temp_factor_beta[3].value*coef_mat[j][0]*coef_mat[k][1]
						+ temp_factor_beta[4].value*coef_mat[j][0]*coef_mat[k][2]
						+ temp_factor_beta[5].value*coef_mat[j][1]*coef_mat[k][2];
			}
		}

		new_temp_factor_beta[0] = new_temp_factor_beta_sym(0,0);
		new_temp_factor_beta[1] = new_temp_factor_beta_sym(1,1);
		new_temp_factor_beta[2] = new_temp_factor_beta_sym(2,2);
		new_temp_factor_beta[3] = new_temp_factor_beta_sym(0,1);
		new_temp_factor_beta[4] = new_temp_factor_beta_sym(0,2);
		new_temp_factor_beta[5] = new_temp_factor_beta_sym(1,2);
		if( m_TemperatureFactorUnit == kZRietveldTemperatureFactorUnitBeta )
		{
			equiv_apos[i].m_AtomTempFactorsAniso = new_temp_factor_beta;
		}
		else
		{
			transformTempFactorFromBeta(m_TemperatureFactorUnit, S, S_covar, true, new_temp_factor_beta, equiv_apos[i].m_AtomTempFactorsAniso);
		}

		if( m_IsSetTempFactors_DefaultConstraints )
		{
			equiv_apos[i].m_IsSetTempFactors_DefaultConstraints = false;
		}
	}
}

void AtomSiteData::SetPhaseData_IO(PhaseData_IO* phase)
{
	m_phase = phase;
}

void AtomSiteData::DebugDescription(zstringstream& stream) const
{
	stream
	<< "Site = " << m_label << endl
	<< "Symbol = " << m_symbol << endl
	<< "Occupancy = " << m_occupancy << endl
	<< "Occupancy ID = " << m_occupancyID << endl
	<< "Position x = " << m_positionX << endl
	<< "Position y = " << m_positionY << endl
	<< "Position z = " << m_positionZ << endl
	<< "Position x ID = " << m_positionIDs[0] << endl
	<< "Position y ID = " << m_positionIDs[1] << endl
	<< "Position z ID = " << m_positionIDs[2] << endl;
	
	switch (m_TemperatureFactorUnit) {
		case kZRietveldTemperatureFactorUnitB:{
			stream << "Temperature factor unit = B" << endl;
		}break;
		case kZRietveldTemperatureFactorUnitU:{
			stream << "Temperature factor unit = U" << endl;
		}break;
		case kZRietveldTemperatureFactorUnitBeta:{
			stream << "Temperature factor unit = β" << endl;
		}break;
	}
	
	if(m_TemperatureFactorIsAnisotropics){
		stream << "Temperature factor = anisotropic" << endl;
	}else{
		stream << "Temperature factor = isotropic" << endl;
	}
	
	stream
	<< "Temperature factor iso = " << m_AtomTempFactorsIso[0] << endl
	<< "Temperature factor aniso 11 = " << m_AtomTempFactorsAniso[0] << endl
	<< "Temperature factor aniso 22 = " << m_AtomTempFactorsAniso[1] << endl
	<< "Temperature factor aniso 33 = " << m_AtomTempFactorsAniso[2] << endl
	<< "Temperature factor aniso 12 = " << m_AtomTempFactorsAniso[3] << endl
	<< "Temperature factor aniso 13 = " << m_AtomTempFactorsAniso[4] << endl
	<< "Temperature factor aniso 23 = " << m_AtomTempFactorsAniso[5] << endl;
	
	if(m_TemperatureFactorIsAnisotropics){
		stream
		<< "Temperature factor aniso 11 ID = " << m_AtomTempFactorBetaIDs[0] << endl
		<< "Temperature factor aniso 22 ID = " << m_AtomTempFactorBetaIDs[1] << endl
		<< "Temperature factor aniso 33 ID = " << m_AtomTempFactorBetaIDs[2] << endl
		<< "Temperature factor aniso 12 ID = " << m_AtomTempFactorBetaIDs[3] << endl
		<< "Temperature factor aniso 13 ID = " << m_AtomTempFactorBetaIDs[4] << endl
		<< "Temperature factor aniso 23 ID = " << m_AtomTempFactorBetaIDs[5] << endl;
	}else{
		stream << "Temperature factor iso ID = " << m_AtomTempFactorBetaIDs[0] << endl;
	}
	
	for(const shared_ptr<ChemicalSpeciesData>& chemicalSpecies : mChemicalSpecieses){
		chemicalSpecies->DebugDescription(stream);
	}
}

void AtomSiteData::DebugDescriptionCollinear(zstringstream& stream) const
{
	stream
	<< "Magnetic symbol = " << m_labelMagnetic << endl
	<< "Spin magnitude = " << m_spinMagnitude << ", ID = " << stringFromID(m_spinMagnitudeID) << endl
	<< "g = " << g << endl;
}

void AtomSiteData::DebugDescriptionPropagationVector(zstringstream& stream) const
{
	stream
	<< "Magnetic symbol = " << m_labelMagnetic << endl
	<< "g = " << g << endl;
}
