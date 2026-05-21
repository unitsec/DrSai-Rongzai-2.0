#ifndef ATOM_GEOMETRY2_HH_
#define ATOM_GEOMETRY2_HH_

#include"../ZRTSpaceGroup_.h"

#include "../RietveldAnalysisTypes.hh"
#include "../zerror_type/error_out.hh"
#include"../utility_data_structure/range.hh"
#include"../utility_data_structure/topo3.hh"
#include"../symmetric_operation/StringS1.hh"
#include"../symmetric_operation/XYZCoord.hh"
#include"../point_group/enumPointGroup.hh"

class AtomSiteData;



typedef struct ZRTCorrectionInfo {
	int translate = 0;
	std::string xyzString = "";
	bool hasFraction = false;
	int sign = 1;
	int numerator = 0;
	int denominator = 0;
} ZRTCorrectionInfo;

typedef struct ZRTPositionCorrectionInfo {
	ZRTCorrectionInfo x;
	ZRTCorrectionInfo y;
	ZRTCorrectionInfo z;
	ZRTCorrectionInfo& GetCorrectionInfo(int xyz);
	const ZRTCorrectionInfo& GetCorrectionInfo(int xyz) const;
} ZRTPositionCorrectionInfo;

typedef struct pos_data {
	Int4 atom_index;
	XYZCoord<StringS1> str_pos;	//並進操作が抜けているので、GetSymmetricOperationStringを代わりに使う
	VecDat3<Double> atom_pos;
	int translateX = 0;
	int translateY = 0;
	int translateZ = 0;
	
	ZRTPositionCorrectionInfo positionCorrectionInfo;

	//並進操作を付加した文字列
	string GetSymmetricOperationString(int xyz) const;
	
	//putAllSite内で、強引に並進操作の文字列を付加するための計算を行う
	//str_posの数式をパースして計算し、atoms::GetPositionが等しくなるように、補正値(=抜けている並進操作)を計算
	ZRTPositionCorrectionInfo TranslateCorrection(double x, double y, double z) const;
} pos_data;


void putAllSite(const ZRTSpaceGroupNumber& ISG,
				const vector<AtomSiteData>& atoms,
				const range<Double>& drange_a,
				const range<Double>& drange_b,
				const range<Double>& drange_c,
				vector<ePointGroup>& stabilizer,
				vector<pos_data>& pos_data_tray);

void putAllPairSite(const ZRTSpaceGroupNumber& ISG,
					const SymMat3<Double>& S_inv,
					const vector<AtomSiteData>& atoms,
					const Double& MAX_L,
					vector< pair< Int4, pos_data> >& pair_pos_data);


bool operator<(const pair< Int4, pos_data>& lhs, const pair< Int4, pos_data>& rhs);

#endif /*ATOM_GEOMETRY2_HH_*/
