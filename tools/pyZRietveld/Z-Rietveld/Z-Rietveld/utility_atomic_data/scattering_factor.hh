#ifndef SCATTERING_FACTOR_HH_
#define SCATTERING_FACTOR_HH_

#include "../RietveldAnalysisTypes.hh"
#include "../zerror_type/error_out.hh"
#include "../ZRTRoot.hh"

class ZRTFilePath;

class scattering_coef_Xray
{
public:
	Double GetA1() const { return a[0]; }
	Double GetA2() const { return a[1]; }
	Double GetA3() const { return a[2]; }
	Double GetA4() const { return a[3]; }
	Double GetB1() const { return a[0]; }
	Double GetB2() const { return b[1]; }
	Double GetB3() const { return b[2]; }
	Double GetB4() const { return b[3]; }
	Double GetC() const { return c; }
	
	Double a[4];
	Double b[4];
	Double c;
};


class ChemicalSpeciesDataForXrays
{
public:
	string Symbol;
	scattering_coef_Xray Scatt_coef_X;
	Double Ratio;
};

Double cal_normal_dispersion_factor_Xray(const Double& dwidth, const scattering_coef_Xray& scat_coef);

// \sum_k scat_coef[i].Ratio[k] should be normalized to 1.
void cal_normal_dispersion_factor_Xray(const Double& dwidth,
											const vector< vector<ChemicalSpeciesDataForXrays> >& scat_coef, vector<Double>& scatt_factor);

// read scattering factor coefficient file and set a1-a4, b1-b4, c
ZErrorMessageReadingFile read_normal_dispersion_factor_coef_Xray(const string& element_name,
										scattering_coef_Xray& scx_tray);

// for X
ZErrorMessageReadingFile read_anomalous_dispersion_factor_Xray(const string& element_name,
											const double& lambda, Double& f_anormal_real, Double& f_anormal_imag);


// flag 0: wide, 1: K, 2: L1, 3: L2, 4: L3
ZErrorMessageReadingFile read_absorption_edge(const string& element_name, const double& lambda, int& flag);

// read widerange file of anormalous dispersion factor
ZErrorMessageReadingFile read_anomalous_dispersion_factor_wide(const string& element_name,
											 const double& lambda,
											 double& f_anormal_real, double& f_anormal_imag);

// read localrange file of anormalous dispersion factor
ZErrorMessageReadingFile read_anomalous_dispersion_factor_local(shared_ptr<ZRTFilePath> filePath, const string& element_name, const double& lambda,
											double& f_anormal_real, double& f_anormal_imag);




#endif /*SCATTERING_FACTOR_HH_*/
