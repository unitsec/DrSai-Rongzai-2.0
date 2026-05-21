#ifndef ATOMICDATA_HH_
#define ATOMICDATA_HH_

#include "../RietveldAnalysisTypes.hh"
#include "../zerror_type/error_out.hh"

typedef struct{
	string ZSymbolA;
	Double AtomicWeight;
	Double presence_rate;
	Double bc;				// bound coherent scattering length(1.0e-5A)
	Double err_bc;			// error_value of bc
	Double sigma_coh;		// bound coherent scattering cross section(1.0e-8A^2)
	Double err_sigma_coh;	// error_value of sigma_coh
	Double sigma_inc;		// bound incoherent scattering cross section(1.0e-8A^2)
	Double err_sigma_inc;	// error_value of sigma_inc
	Double sigma_scatt;		// total bound scattering cross section(1.0e-8A^2)
	Double err_sigma_scatt;	// error_value of sigma_scatt
	Double sigma_abs;		// absorption cross section(1.0e-8A^2) for neutrons of wavelength λ＝1.8A
	Double err_sigma_abs;	// error_value of sigma_abs
} atomic_data;

const atomic_data* AtomicData(const string& symbol);

const atomic_data& AtomicData(const Int4&);
ZRTErrorType IntAtomicSymbol(const string&, Int4&);

string ConvAtomicSymbol(const string atom_name);

#endif /*ATOMICDATA_HH_*/
