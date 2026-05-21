#include <set>

#include "atom_geometry2.hh"
#include "atom_geometry.hh"
#include "AtomSiteData.hh"
#include "../utility_data_structure/index_set.hh"
#include "../symmetric_operation/SymmetricOperation.hh"
#include "../put_atom_pos_multiplicity.hh"
#include "../symmetric_operation/StringS1.hh"
#include "../symmetric_operation/XYZCoord.hh"

using namespace std;

ZRTCorrectionInfo& ZRTPositionCorrectionInfo::GetCorrectionInfo(int xyz)
{
	switch (xyz) {
		case 0:{
			return x;
		}break;
		case 1:{
			return y;
		}break;
		case 2:{
			return z;
		}break;
		default:abort();
	}
}

const ZRTCorrectionInfo& ZRTPositionCorrectionInfo::GetCorrectionInfo(int xyz) const
{
	switch (xyz) {
		case 0:{
			return x;
		}break;
		case 1:{
			return y;
		}break;
		case 2:{
			return z;
		}break;
		default:abort();
	}
}


void putAllSite(const ZRTSpaceGroupNumber& ISG,
				const vector<AtomSiteData>& atoms,
				const range<Double>& drange_a,
				const range<Double>& drange_b,
				const range<Double>& drange_c,
				vector<ePointGroup>& stabilizer,
				vector<pos_data>& pos_data_tray)
{
	pos_data_tray.clear();

	const Int4 atom_num = (Int4)atoms.size();
	stabilizer.resize(atom_num);
	
	static const Double thred = 1.0e-3;
//	static const Int4 PN_XYZ = PNXYZ();

	vector< XYZCoord<S1> > apos_equiv;
	vector< XYZCoord<StringS1> > coset_rep;
	Int4 num_site_symmetry, atom_pos_multiplicity;

	range<Int4> lattice_irange[3];
	Double rem_begin[3];
	Double rem_end[3];
	
	lattice_irange[0].begin = iceil( drange_a.begin );
	lattice_irange[0].end = ifloor( drange_a.end );
	lattice_irange[1].begin = iceil( drange_b.begin );
	lattice_irange[1].end = ifloor( drange_b.end );
	lattice_irange[2].begin = iceil( drange_c.begin );
	lattice_irange[2].end = ifloor( drange_c.end );

	rem_begin[0] = drange_a.begin - lattice_irange[0].begin + 1.0;
	rem_end[0] = drange_a.end - lattice_irange[0].end;
	rem_begin[1] = drange_b.begin - lattice_irange[1].begin + 1.0;
	rem_end[1] = drange_b.end - lattice_irange[1].end;
	rem_begin[2] = drange_c.begin - lattice_irange[2].begin + 1.0;
	rem_end[2] = drange_c.end - lattice_irange[2].end;

	VecDat3<Int4> istart, iend;
	VecDat3<Double> apos;
	pos_data tray;
	
	try{
		for(Int4 j=0; j<atom_num; j++)
		{
			const VecDat3<Double> apos0(atoms[j].GetPositionX().value, atoms[j].GetPositionY().value, atoms[j].GetPositionZ().value);
			put_atom_pos_multiplicity(ISG, apos0, apos_equiv, coset_rep, stabilizer[j], num_site_symmetry, atom_pos_multiplicity);
			
			tray.atom_index = j;
			
			for(UInt4 i=0; i<apos_equiv.size(); i++)
			{
				const XYZCoord<S1>& spos = apos_equiv[i];
				for(Int4 l=0; l<PNXYZ(); l++)
				{
					istart[l] = lattice_irange[l].begin;
					iend[l] = lattice_irange[l].end;

					apos[l] = spos[l].toDouble();
					if( apos[l] + thred >= rem_begin[l] ) istart[l]--;
					if( apos[l] <= rem_end[l] + thred  ) iend[l]++;

				}
				tray.str_pos = coset_rep[i];
				tray.atom_pos[0] = apos[0];
				tray.atom_pos[1] = apos[1];
				tray.atom_pos[2] = apos[2];

				ZRTPositionCorrectionInfo correctionInfo = tray.TranslateCorrection(atoms[j].GetPositionX().value, atoms[j].GetPositionY().value, atoms[j].GetPositionZ().value);
				tray.positionCorrectionInfo = correctionInfo;

				for(Int4 translateX=istart[0]; translateX<iend[0]; translateX++)
				{ 
//					tray.trans_vec[0] = k0;
					tray.atom_pos[0] = apos[0] + translateX;
					tray.translateX = translateX;
					for(Int4 translateY=istart[1]; translateY<iend[1]; translateY++)
					{
//						tray.trans_vec[1] = k1;
						tray.atom_pos[1] = apos[1] + translateY;
						tray.translateY = translateY;

						for(Int4 translateZ=istart[2]; translateZ<iend[2]; translateZ++)
						{
//							tray.trans_vec[2] = k2;
							tray.atom_pos[2] = apos[2] + translateZ;
							tray.translateZ = translateZ;

							pos_data_tray.push_back( tray );
						}
					}
				}
			}
		}
	}
	catch(bad_alloc& ball)
	{
	    throw nerror(ball, __FILE__, __LINE__, __FUNCTION__);
	}
}


inline bool operator<(const pos_data& lhs, const pos_data& rhs)
{
	const VecDat3<Double>& lhs_pos = lhs.atom_pos;
	const VecDat3<Double>& rhs_pos = rhs.atom_pos;

	if( lhs_pos[0] < rhs_pos[0] ) return true;
	if( rhs_pos[0] < lhs_pos[0] ) return false;

	if( lhs_pos[1] < rhs_pos[1] ) return true;
	if( rhs_pos[1] < lhs_pos[1] ) return false;

	if( lhs_pos[2] < rhs_pos[2] ) return true;
	return false;
}

string pos_data::GetSymmetricOperationString(int xyz) const {
	string symmetricOperationString = "";
	int value = 0;
	const ZRTCorrectionInfo& correctionInfo = positionCorrectionInfo.GetCorrectionInfo(xyz);
	symmetricOperationString += correctionInfo.xyzString;
	
	switch (xyz) {
		case 0:{
			value = translateX + correctionInfo.translate;
		}break;
		case 1:{
			value = translateY + correctionInfo.translate;
		}break;
		case 2:{
			value = translateZ + correctionInfo.translate;
		}break;
	}

	if(correctionInfo.hasFraction){
		if(-1 >= value){
			if(1 == correctionInfo.sign){
				symmetricOperationString += "-";
			}else{
				symmetricOperationString += "+";
			}
			symmetricOperationString += to_string(-correctionInfo.numerator - value*correctionInfo.denominator);	//分子
		}else{
			if(1 == correctionInfo.sign){
				symmetricOperationString += "+";
			}else{
				symmetricOperationString += "-";
			}
			symmetricOperationString += to_string(correctionInfo.numerator + value*correctionInfo.denominator);	//分子
		}
		symmetricOperationString += "/" + to_string(correctionInfo.denominator);	//分母
	}else{
		if(value > 0){
			symmetricOperationString += "+" + to_string(value);
		}else if(value < 0){
			symmetricOperationString +=  to_string(value);
		}
	}
	return symmetricOperationString;
}

ZRTPositionCorrectionInfo pos_data::TranslateCorrection(double x, double y, double z) const
{
	ZRTPositionCorrectionInfo positionCorrectionInfo;
	for(int xyz=0; xyz<3; xyz++){
		ZRTCorrectionInfo& correctionInfo = positionCorrectionInfo.GetCorrectionInfo(xyz);
		string string = str_pos[xyz].toString();
		size_t length = string.length();
		
		double sign = 1;
		double total = 0;
		double value = 0;
		bool isNumerator = true;
		double numerator = 0;
		double denominator = 0;
		for(int index=0; index<length; index++){
			char character = string[index];
			switch (character) {
				case '-':{
					sign = -1;
				}break;
				case '+':{
					sign = 1;
				}break;
				case 'x':{
					value = x;
					total += sign * value;
					correctionInfo.xyzString = ((sign == -1)? "-" : "");
					correctionInfo.xyzString += "x";
					sign = 1;
				}break;
				case 'y':{
					value = y;
					total += sign * value;
					correctionInfo.xyzString = ((sign == -1)? "-" : "");
					correctionInfo.xyzString += "y";
					sign = 1;
				}break;
				case 'z':{
					value = z;
					total += sign * value;
					correctionInfo.xyzString = ((sign == -1)? "-" : "");
					correctionInfo.xyzString += "z";
					sign = 1;
				}break;
				case '0':
				case '1':
				case '2':
				case '3':
				case '4':
				case '5':
				case '6':
				case '7':
				case '8':
				case '9':{
					if(isNumerator){
						numerator = character - 48;	//ASCIIから数値に変換
						correctionInfo.numerator = numerator;
					}else{
						denominator = character - 48;	//ASCIIから数値に変換
						total += sign * (numerator/denominator);
						correctionInfo.sign = sign;
						correctionInfo.denominator = denominator;
						correctionInfo.hasFraction = true;
						isNumerator = true;
						sign = 1;
					}
				}break;
				case '/':{
					isNumerator = false;
				}break;
				default:{
				}break;
			}
		}
		double diffDouble = atom_pos[xyz] - total;
		int diff = round(diffDouble);
		correctionInfo.translate = diff;
	}
	return positionCorrectionInfo;
}

void putAllPairSite(const ZRTSpaceGroupNumber& ISG,
					const SymMat3<Double>& S_inv,
					const vector<AtomSiteData>& atoms,
					const Double& MAX_L,
					vector< pair< Int4, pos_data> >& pair_pos_data)
{
	pair_pos_data.clear();

	const Double MAX_L_SQUARE = MAX_L * MAX_L;
	
	range<Double> drange_a, drange_b, drange_c;
	vector<ePointGroup> stabilizer;
	vector<pos_data> pos_data_tray;

	drange_a.begin = -1.0; drange_a.end = 2.0;
	drange_b.begin = -1.0; drange_b.end = 2.0;
	drange_c.begin = -1.0; drange_c.end = 2.0;
	putAllSite(ISG, atoms, drange_a, drange_b, drange_c, stabilizer, pos_data_tray);
	
	sort(pos_data_tray.begin(), pos_data_tray.end());
	
	pos_data apos_begin, apos_end;
	apos_begin.atom_pos = -1.0;
	apos_end.atom_pos = 2.0;
	
	vector< SymmetricOperation > stabilizer_sym;
	vector< XYZCoord<StringS1> > stabilizer_str;
	vector<pos_data>::const_iterator it, it_begin, it_end;
	Double distsq;
//	Int4 index;
	pair< Int4, pos_data> pair_pos_tray;

	const Int4 atom_num = (Int4)atoms.size();
	const XYZCoord<StringS1> str_id(StringS1::putX(), StringS1::putY(), StringS1::putZ());
	
//	vector< set< XYZCoord<StringS1> > > found_pair_tray[3];
	try{
		for(Int4 k=0; k<atom_num; k++)
		{
			const VecDat3<Double> apos(atoms[k].GetPositionX().value,
									   atoms[k].GetPositionY().value,
									   atoms[k].GetPositionZ().value);

			putAllElement(stabilizer[k], stabilizer_sym);
			put_stabilizer_trans_vector(ISG, apos, stabilizer_sym, stabilizer_str);
			
			apos_begin.atom_pos[0] = apos[0] - MAX_L;
			apos_end.atom_pos[0] = apos[0] + MAX_L;
			it_begin = lower_bound(pos_data_tray.begin(), pos_data_tray.end(), apos_begin);
			it_end = upper_bound(it_begin, (vector<pos_data>::const_iterator)pos_data_tray.end(), apos_end);
			
//			found_pair_tray.clear();
//			found_pair_tray.resize(atom_num-k);
//			found_pair_tray[0].insert( XYZCoord<StringS1>(StringS1::putX(), StringS1::putY(), StringS1::putZ()) );	// identity element.
			
			for(it=it_begin; it!=it_end; it++)
			{
//				if( it->atom_index < k  ) continue;
				if( it->atom_pos[1] + MAX_L < apos[1] || apos[1] + MAX_L < it->atom_pos[1] ) continue;
				if( it->atom_pos[2] + MAX_L < apos[2] || apos[2] + MAX_L < it->atom_pos[2] ) continue;
				if( it->atom_index == k && it->str_pos == str_id  ) continue;

				distsq = calAtomDistanceSquare(apos, it->atom_pos, S_inv);
				if( distsq > MAX_L_SQUARE ) continue;

//				index = it->atom_index - k;
//				if( found_pair_tray[index].end() != found_pair_tray[index].find(it->str_pos) ) continue;

				pair_pos_tray.first = k;
				pair_pos_tray.second = *it;
				pair_pos_data.push_back(pair_pos_tray);

/*				
				for(UInt4 l=0; l<stabilizer_str.size(); l++)
				{
					found_pair_tray[index].insert( it->str_pos * stabilizer_str[l] );
				}
*/
			}
		}
		
		sort(pair_pos_data.begin(), pair_pos_data.end());
	}
	catch(bad_alloc& ball)
	{
	    throw nerror(ball, __FILE__, __LINE__, __FUNCTION__);
	}
}

bool operator<(const pair< Int4, pos_data>& lhs, const pair< Int4, pos_data>& rhs)
{
	if( lhs.first < rhs.first ) return true;
	if( rhs.first < lhs.first ) return false;
	
	if( lhs.second.atom_index < rhs.second.atom_index ) return true;
	if( rhs.second.atom_index < lhs.second.atom_index ) return false;
	
	const VecDat3<Double>& lhs_pos = lhs.second.atom_pos;
	const VecDat3<Double>& rhs_pos = rhs.second.atom_pos;
	
	if( lhs_pos[0] < rhs_pos[0] ) return true;
	if( rhs_pos[0] < lhs_pos[0] ) return false;
	
	if( lhs_pos[1] < rhs_pos[1] ) return true;
	if( rhs_pos[1] < lhs_pos[1] ) return false;
	
	if( lhs_pos[2] < rhs_pos[2] ) return true;
	return false;
}

