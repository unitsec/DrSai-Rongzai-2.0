#include <algorithm>
#include <cctype>
#include <fstream>

#include "../ZRTLibFolder_.h"

#include "../utility_func/zstring.hh"
#include "../zfoundation/zostream.h"
#include "scattering_factor.hh"
#include "../ZRTFilePath.hh"
#include "../ZRTLibFolder.hh"

// for X
// input: element name, sin theta/lambda
// f = \sum { a_i exp( -b_i (sin theta/lambda)^2 } + c
static Double cal_normal_dispersion_factor(const scattering_coef_Xray& scat_coef,
const Double& sin_theta_lambda)
{
	double f = 0.0;
	double sin_th_lambda_sq = sin_theta_lambda * sin_theta_lambda;
	
	for( int i=0; i<4; i++ )
	{
		f += scat_coef.a[i] * exp( -1.0 * scat_coef.b[i] * sin_th_lambda_sq );
	}

	return f + scat_coef.c;
}


Double cal_normal_dispersion_factor_Xray(const Double& dwidth, const scattering_coef_Xray& scat_coef)
{
	return cal_normal_dispersion_factor(scat_coef, 0.5/dwidth);	// unit -> Angstrom
}


void cal_normal_dispersion_factor_Xray(const Double& dwidth,
const vector< vector<ChemicalSpeciesDataForXrays> >& scat_coef, vector<Double>& scatt_factor)
{
	const Int4 atom_num = (Int4)scat_coef.size();

	scatt_factor.resize(atom_num);
	
	for(Int4 k=0; k<atom_num; k++)
	{
		scatt_factor[k] = 0.0;
		for(vector<ChemicalSpeciesDataForXrays>::const_iterator it=scat_coef[k].begin(); it<scat_coef[k].end(); it++)
		{
			scatt_factor[k] += cal_normal_dispersion_factor(it->Scatt_coef_X, 0.5/dwidth) * it->Ratio;	// unit -> Angstrom
		}
	}
}


static ZErrorMessageReadingFile read_file_normal_dispersion_factor_coef_Xray(const string& element_name,
		scattering_coef_Xray& scx_tray)
{
	//fopen
	ifstream ifs( ZRTLibFolder::GetScatteringFactorFilePath()->GetPath().c_str() );
    if (!ifs){
     	return ZErrorMessageReadingFile(string(ZRTLibFolderScatteringFactorFileName), nerror( ZRTErrorTypeFileNotFound, __FILE__, __LINE__, __FUNCTION__));
    }

	string s,t;
	double u;
	vector<double> scattering_factor_coefficient(9, 0.0);

	while (getnewline(ifs, s) == ZRTErrorTypeNoError)
	{
		istringstream iss(s);

		if (s.empty()) { continue; }

       	iss >> t;
      	if( iss.fail() ){
    		return ZErrorMessageReadingFile(string(ZRTLibFolderScatteringFactorFileName), nerror( ZRTErrorTypeFileFormatBroken, __FILE__, __LINE__, __FUNCTION__));
        }

		if (t != element_name) { continue; }
		else {
			iss >> t;  // method
			for( int i = 0; i < 9 ; i++) {
				iss >> u;
	        	if( iss.fail() ){
	        		return ZErrorMessageReadingFile(string(ZRTLibFolderScatteringFactorFileName), nerror( ZRTErrorTypeFileFormatBroken, __FILE__, __LINE__, __FUNCTION__));
				}
				scattering_factor_coefficient[i] = u;
			}

			scx_tray.a[0] = scattering_factor_coefficient[0]; scx_tray.b[0] = scattering_factor_coefficient[1];
			scx_tray.a[1] = scattering_factor_coefficient[2]; scx_tray.b[1] = scattering_factor_coefficient[3];
			scx_tray.a[2] = scattering_factor_coefficient[4]; scx_tray.b[2] = scattering_factor_coefficient[5];
			scx_tray.a[3] = scattering_factor_coefficient[6]; scx_tray.b[3] = scattering_factor_coefficient[7];
			scx_tray.c    = scattering_factor_coefficient[8];

			return ZErrorMessageReadingFile();
		}
    }

	return ZErrorMessageReadingFile(string(ZRTLibFolderScatteringFactorFileName), nerror(ZRTErrorTypeScatteringFactorUndefined8, element_name + " was not found in " + ZRTLibFolderScatteringFactorFileName, __FILE__, __LINE__, __FUNCTION__));
}



ZErrorMessageReadingFile read_normal_dispersion_factor_coef_Xray(const string& element_name,
		scattering_coef_Xray& scx_tray)
{
	// si, SI -> Si
	string aname0 = element_name;
	aname0.at(0) = toupper( element_name.at(0) );
	for(UInt4 i=1; i<element_name.length(); i++) aname0.at(i) = tolower( element_name.at(i) );

	if( aname0 == "Vc")
	{
		scx_tray.a[0] = 0.0;
		scx_tray.a[1] = 0.0;
		scx_tray.a[2] = 0.0;
		scx_tray.a[3] = 0.0;
		scx_tray.b[0] = 0.0;
		scx_tray.b[1] = 0.0;
		scx_tray.b[2] = 0.0;
		scx_tray.b[3] = 0.0;
		scx_tray.c = 0.0;
		return ZErrorMessageReadingFile();
	}
	return read_file_normal_dispersion_factor_coef_Xray(aname0, scx_tray);
}


ZErrorMessageReadingFile read_anomalous_dispersion_factor_Xray(const string& element_name, const double& lambda, Double& f_anormal_real, Double& f_anormal_imag)
{
	string atom_name;

	if( element_name.empty() ) return ZErrorMessageReadingFile("", nerror(ZRTErrorTypeAtomIsEmpty, "Atom name is empty", __FILE__, __LINE__, __FUNCTION__));

	const Int4 str_len = (Int4)element_name.length();

	if( str_len > 3 && element_name.substr( str_len-3, str_len ) == "val" )
	{
		atom_name = element_name.substr( 0, str_len-3 );	// "*val" => "*"
	}
	else if( element_name.at( str_len-1 ) == '+' || element_name.at( str_len-1 ) == '-' )
	{
		if( str_len < 2 ) return ZErrorMessageReadingFile("", nerror_out_range("atomName", element_name, __FILE__, __LINE__, __FUNCTION__));
		else if( isdigit( element_name.at( str_len-2 ) ) ) atom_name = element_name.substr( 0, str_len-2 );	// "*4+" => "*", 	// "*4-" => "*"
		else atom_name = element_name.substr( 0, str_len-1 );	// "*+" => "*", 	// "*-" => "*"
	}
//	else if( str_len.at( str_len-1 ) == 'v' && )
//	{
//	}
	else {
		atom_name = element_name;
	}

	// convert capital letter
	// ( "Si" -> "SI" )
	transform(atom_name.begin(), atom_name.end(), atom_name.begin(), (int (*)(int))std::toupper);

	int flag = 0;
	f_anormal_real=0.0, f_anormal_imag=0.0;

	ZErrorMessageReadingFile zerr = read_absorption_edge(atom_name, lambda, flag);
	if( ZRTErrorTypeNoError != zerr.putErrorType() ) return zerr;
	
	if(flag == 0) {
		return read_anomalous_dispersion_factor_wide(atom_name, lambda, f_anormal_real, f_anormal_imag);
	}
	if(flag == 1) {
		return read_anomalous_dispersion_factor_local(ZRTLibFolder::GetKTBLFilePath(), atom_name, lambda, f_anormal_real, f_anormal_imag);
	}
	if(flag == 2) {
		return read_anomalous_dispersion_factor_local(ZRTLibFolder::GetL1TBLFilePath(), atom_name, lambda, f_anormal_real, f_anormal_imag);
	}
	if(flag == 3) {
		return read_anomalous_dispersion_factor_local(ZRTLibFolder::GetL2TBLFilePath(), atom_name, lambda, f_anormal_real, f_anormal_imag);
	}
	// if(flag == 4) {
	return read_anomalous_dispersion_factor_local(ZRTLibFolder::GetL3TBLFilePath(), atom_name, lambda, f_anormal_real, f_anormal_imag);
}



ZErrorMessageReadingFile read_absorption_edge(const string& element_name, const double& lambda,
int &flag)
{
	ifstream ifs( ZRTLibFolder::GetAbsorptionEdgeFilePath()->GetPath().c_str() );
    if (!ifs){
     	return ZErrorMessageReadingFile(ZRTLibFolderAbsorptionEdgeFileName, nerror( ZRTErrorTypeFileNotFound, __FILE__, __LINE__, __FUNCTION__));
    }

    try {

		int find_flag = 0;
		double lambda_k, lambda_l1,  lambda_l2, lambda_l3;

		string s, str;

	    while (getnewline(ifs, s) == ZRTErrorTypeNoError)
	    {
//    	    addLineCounter(file_index);
        	if ( is_blank(s) ) continue;
    		if ( s.at(0) == '*' ) continue;
			if ( s.at(0) == '#' ) continue;

        	istringstream iss(s);

			iss >> str;
			if(iss.fail()) { throw(1); }  // Atom No

			iss >> str;
			if( str == element_name ) {
				iss >> lambda_k;
				if(iss.fail()) { throw(1); }  // read K edge
				iss >> lambda_l1;
				if(iss.fail()) { throw(1); }  // read L1 edge
				iss >> lambda_l2;
				if(iss.fail()) { throw(1); }  // read L2 edge
				iss >> lambda_l3;
				if(iss.fail()) { throw(1); }  // read L3 edge

				find_flag = 1;
				break;
			}
			else {
				continue;
			}
		}

		if( !find_flag ) {
			flag = 0;
		}
		else {
			if( fabs( lambda - lambda_k )  < 0.014 )      { flag = 1; }
			else if( fabs( lambda - lambda_l1 ) < 0.014 ) { flag = 2; }
			else if( fabs( lambda - lambda_l2 ) < 0.014 ) { flag = 3; }
			else if( fabs( lambda - lambda_l3 ) < 0.014 ) { flag = 4; }
			else { flag = 0; }
		}

    }

    catch(int){
       	return ZErrorMessageReadingFile(ZRTLibFolderAbsorptionEdgeFileName, nerror(ZRTErrorTypeFileFormatBroken,  __FILE__, __LINE__, __FUNCTION__));
    }
	return ZErrorMessageReadingFile();
}




ZErrorMessageReadingFile read_anomalous_dispersion_factor_wide(const string& element_name,
const double& lambda, double &f_anormal_real, double &f_anormal_imag)
{
//zcout << "element : " << element_name << endl;
//zcout << "lambda : " << lambda << endl;
	//fopen
	ifstream ifs( ZRTLibFolder::GetWideTBLFilePath()->GetPath().c_str() );
    if (!ifs) {
     	return ZErrorMessageReadingFile(ZRTLibFolderWideTBLFileName, nerror( ZRTErrorTypeFileNotFound, __FILE__, __LINE__, __FUNCTION__));
	}

	try {

		string s,t;
		double a, b, delta = 0.0;
		double y1, y2;
		int flag = 0;
		int num = 0;

		while (getnewline(ifs, s) == ZRTErrorTypeNoError)
		{
//    	    addLineCounter(file_index);
			if ( is_blank(s) ) continue;
			if ( s.at(0) == '*' ) continue;
			if ( s.at(0) == '#' ) continue;

			if(s.substr(0,15) == "ATOMIC SYMBOL =") {
				istringstream iss(s.substr(15));

				iss >> t;  // Atom

				if( iss.fail() ){ throw(1); }

				if( t == element_name ) {
					flag = 1; 
					break;
				}
			}
		}
		if( !flag ) throw(3);

		// when target is found
		while (getnewline(ifs, s) == ZRTErrorTypeNoError)
		{
//    	    addLineCounter(file_index);
			if ( is_blank(s) ) continue;
			if ( s.at(0) == '*' ) continue;
			if ( s.at(0) == '#' ) continue;

			istringstream iss(s);

			if(flag == 1)
			{
				iss >> a;
				if( iss.fail() ) throw (1);
				iss >> t;
				iss >> b;   // 1.00 / TO / 1.09  ( widetbl.dat format )
				if( iss.fail() ) throw (1);

				if( a <= lambda && a + 0.1 > lambda )
				{
					iss >> t;  // F'
					if( t != "F\'" ) { throw(1); }

					delta = ( b - a ) / 9;
					num = (Int4)floor( (lambda-a)/delta );
					
					for( int i=0; i<num; i++) iss >> y1;

					iss >> y1;
					if( iss.fail() ) throw (1);
					
					if( num < 9 )
					{
						iss >> y2;
						if( iss.fail() ) throw (1);

						f_anormal_real = y1 + ( y2 - y1 ) * ( lambda - a - delta * num ) / delta;
//#ifdef DEBUG
//	zcout << "F' : [" << y1 << ", " << y2 << "]" << endl;
//#endif
					}
					else
					{	
						f_anormal_real = y1;
					}
					flag = 2;
					break;
				}
				else
				{
					flag = 0;
				}
			}
			else
			{
				flag = 1;
			}
		}
		if( flag != 2 ) { throw(4); }

		flag = 0;
		while (getnewline(ifs, s) == ZRTErrorTypeNoError)
		{
//    	    addLineCounter(file_index);
			if ( is_blank(s) ) continue;
			if ( s.at(0) == '*' ) continue;
			if ( s.at(0) == '#' ) continue;

			istringstream iss(s);

			iss >> t;  // F"
			if( t != "F\"" ) { throw(1); }


			for( int i=0; i<num; i++) iss >> y1;

			iss >> y1;
			if( iss.fail() ) throw (1);
					
			if( num < 9 )
			{
				iss >> y2;
				if( iss.fail() ) throw (1);

				f_anormal_imag = y1 + ( y2 - y1 ) * ( lambda - a - delta * num ) / delta;
//#ifdef DEBUG
//	zcout << "F\" : [" << y1 << ", " << y2 << "]" << endl;
//#endif
				return ZErrorMessageReadingFile();
			}
			else
			{	
				f_anormal_imag = y1;
				flag = 1;
				break;
			}
		}
		if( !flag ) { throw(4); }
		
		// when .09 <= lambda < .00
		Double b2;
		while (getnewline(ifs, s) == ZRTErrorTypeNoError)
		{
//    	    addLineCounter(file_index);
			if ( is_blank(s) ) continue;
			if ( s.at(0) == '*' ) continue;
			if ( s.at(0) == '#' ) continue;

			istringstream iss(s);

			if(flag == 1)
			{
				iss >> a;
				if( iss.fail() ) throw (1);
				iss >> t;
				iss >> b2;   // 1.00 / TO / 1.09  ( widetbl.dat format )
				if( iss.fail() ) throw (1);

				iss >> t;  // F'
				if( t != "F\'" ) { throw(1); }

				delta = a-b;
				
				iss >> y1;
				if( iss.fail() ) throw (1);
					
				f_anormal_real += ( y1 - f_anormal_real )*(lambda-b)/delta;
//#ifdef DEBUG
//	zcout << "F' : [" << f_anormal_real << ", " << y1 << "]" << endl;
//#endif
				flag = 2;
			}
			else
			{
				iss >> t;  // F"
				if( t != "F\"" ) { throw(1); }

				iss >> y1;
				if( iss.fail() ) throw (1);
					
				f_anormal_imag += ( y1 - f_anormal_imag )*(lambda-b)/delta;
//#ifdef DEBUG
//	zcout << "F\" : [" << f_anormal_imag << ", " << y1 << "]" << endl;
//#endif
				return ZErrorMessageReadingFile();
			}
		}
		throw(1);

	}
    catch(int num)
    {
    	if(num == 1) {
	       	return ZErrorMessageReadingFile(ZRTLibFolderWideTBLFileName, nerror(ZRTErrorTypeFileFormatBroken, __FILE__, __LINE__, __FUNCTION__));
    	}
		else if( num == 2) {
	       	return ZErrorMessageReadingFile(ZRTLibFolderWideTBLFileName, nerror(ZRTErrorTypeScatteringFactorUndefined3, "WRONG DATA FOR "+element_name, __FILE__, __LINE__, __FUNCTION__));
		}
		else if( num == 3) {
	       	return ZErrorMessageReadingFile(ZRTLibFolderWideTBLFileName, nerror(ZRTErrorTypeScatteringFactorUndefined4, "NO DATA FOR "+element_name, __FILE__, __LINE__, __FUNCTION__));
		}
		else if( num == 4 ) {
	       	return ZErrorMessageReadingFile(ZRTLibFolderWideTBLFileName, nerror(ZRTErrorTypeScatteringFactorUndefined5, "NO DATA For Wave-length "+num2str(lambda)+" FOR "+element_name, __FILE__, __LINE__, __FUNCTION__));
		}
    	else {
	       	return ZErrorMessageReadingFile(ZRTLibFolderWideTBLFileName, nerror(ZRTErrorTypeScatteringFactorUndefined1, __FILE__, __LINE__, __FUNCTION__));
	    }
    }
	return ZErrorMessageReadingFile();
}



ZErrorMessageReadingFile read_anomalous_dispersion_factor_local(shared_ptr<ZRTFilePath> filePath, const string& element_name,
const double& lambda, double &f_anormal_real, double &f_anormal_imag)
{
	string filename = "ktbl.dat, l1tbl.dat, l2tbl.dat, l3tbl.dat";
	//fopen
	ifstream ifs( filePath->GetPath().c_str() );
    if (!ifs) {
     	return ZErrorMessageReadingFile(filename, nerror( ZRTErrorTypeFileNotFound, __FILE__, __LINE__, __FUNCTION__));
     }

	try {
		string s,t;
		double a, b, delta = 0.0;
		double y1, y2;
		int flag = 0;
		int num = 0;

		while (getnewline(ifs, s) == ZRTErrorTypeNoError)
		{
//    	    addLineCounter(file_index);
			if ( is_blank(s) ) continue;
			if ( s.at(0) == '*' ) continue;
			if ( s.at(0) == '#' ) continue;

			istringstream iss(s);

			if(s.substr(0,15) == "ATOMIC SYMBOL =") {
				iss >> t; iss >> t; iss >> t;  // ATOMIC / SYMBOL / = /
				iss >> t;  // Atom

				if( iss.fail() ){ throw(1); }

				if( t == element_name ) {
					flag = 1; 
					continue;
				}
				if( flag == 1 ) { throw(2); }
			}

			// when target is found
			if(flag == 1)
			{
				iss >> a; iss >> b;   // 1.0000 / 1.0009  ( ktbl.dat format )

				if( a <= lambda && b > lambda ) {
					iss >> t;  // F'

					if( t != "F\'" ) { throw(1); }

					delta = ( b - a ) / 9;
				
					for( int i=0; i<10; i++ ) {
						iss >> y1;
						if( a <= lambda && a+delta > lambda ) {
							iss >> y2;

							f_anormal_real = y1 + ( y2 - y1 ) * ( lambda - a ) / delta;
							num = i;
							flag = 2;
							break;
						}
						a += delta;
					}

					if( flag == 2 ) { continue; } // go to F" term
					else { throw(1); }

				}
				else {
					flag = 3;
					continue;
				}
			}

			if( flag == 2 ) {
				iss >> t;

				if( t != "F\"" ) { throw(1); }

				for( int i=0; i<num; i++ ) {
					iss >> y1;
				}

				iss >> y1;
				iss >> y2;
				f_anormal_imag = y1 + ( y2 - y1 ) * ( lambda - a ) / delta;

//				setFinishReadingFile(file_index);
				return ZErrorMessageReadingFile();
			}

			if( flag == 3 ) {
				iss >> t;
				if( t != "F\"" ) { throw(1); }
				else { 
					flag = 1;
					continue;  // go to next F' term
				}
			}


			if( iss.fail() ){ throw(1); }
		}

		if(flag == 0 ) {throw(3); }
		else { throw(2); }
	}

    catch(int num){
    	if(num == 1) {
	       	return ZErrorMessageReadingFile(filename, nerror(ZRTErrorTypeFileFormatBroken, __FILE__, __LINE__, __FUNCTION__) );
    	}
		else if( num == 2) {
	       	return ZErrorMessageReadingFile(filename, nerror(ZRTErrorTypeScatteringFactorUndefined7, "WRONG DATA FOR "+element_name, __FILE__, __LINE__, __FUNCTION__) );
		}
		else if( num == 3) {
	       	return ZErrorMessageReadingFile(filename, nerror(ZRTErrorTypeScatteringFactorUndefined6, "NO DATA FOR "+element_name, __FILE__, __LINE__, __FUNCTION__) );
		}
    	else {
	       	return ZErrorMessageReadingFile(filename, nerror(ZRTErrorTypeScatteringFactorUndefined2, __FILE__, __LINE__, __FUNCTION__) );
	    }
    }
	return ZErrorMessageReadingFile();
}

