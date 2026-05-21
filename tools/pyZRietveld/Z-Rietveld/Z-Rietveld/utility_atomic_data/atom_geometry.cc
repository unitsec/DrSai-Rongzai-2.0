#include "atom_geometry.hh"
#include "../ZRTConstants_.h"

// On input, the argument atom_pos_covar is the covariant matrix on xyz1, xyz2.
// (Hence, it is a square matrix of size 6.)
Double calAtomDistanceSquare(const VecDat3<Double>& xyz1, const VecDat3<Double>& xyz2, const SymMat3<Double>& S_inv,
		const SymMat<Double>& S_inv_covar, const SymMat<Double>& atom_pos_covar, Double& var)
{
	// norm = S11*(x1-x2)^2 + S22*(y1-y2)^2 + S33*(z1-z2)^2
	//	+ 2.0 * ( S12*(x1-x2)*(y1-y2) + S13*(x1-x2)*(z1-z2) + S23*(y1-y2)*(z1-z2) )
	// dnorm/dA* = (x1-x2)^2.
	// dnorm/dB* = (y1-y2)^2.
	// dnorm/dC* = (z1-z2)^2.
	// dnorm/dD* = 2.0*(y1-y2)*(z1-z2).
	// dnorm/dE* = 2.0*(x1-x2)*(z1-z2).
	// dnorm/dF* = 2.0*(x1-x2)*(y1-y2).
	const VecDat3<Double> diff = xyz1 - xyz2;
	const Double coef_lat[6] = {diff[0]*diff[0], diff[1]*diff[1], diff[2]*diff[2],
												2.0*diff[1]*diff[2], 2.0*diff[0]*diff[2], 2.0*diff[0]*diff[1]}; 

	// dnorm/dx1 = 2.0 * ( S11*(x1-x2) + S12*(y1-y2) + S13*(z1-z2) ).
	// dnorm/dy1 = 2.0 * ( S22*(y1-y2) + S12*(x1-x2) + S23*(z1-z2) ).
	// dnorm/dz1 = 2.0 * ( S33*(z1-z2) + S13*(x1-x2) + S23*(y1-y2) ).
	// dnorm/dx2 = 2.0 * ( S11*(x2-x1) + S12*(y2-y1) + S13*(z2-z1) ) = -dnorm/dx1.
	// dnorm/dy2 = 2.0 * ( S22*(y2-y1) + S12*(x2-x1) + S23*(z2-z1) ) = -dnorm/dy1.
	// dnorm/dz2 = 2.0 * ( S33*(z2-z1) + S13*(x2-x1) + S23*(y2-y1) ) = -dnorm/dz1.

	const Double coef_pos[3] = { 2.0 * ( S_inv(0,0)*diff[0] + S_inv(0,1)*diff[1] + S_inv(0,2)*diff[2] ),
								 2.0 * ( S_inv(1,1)*diff[1] + S_inv(0,1)*diff[0] + S_inv(1,2)*diff[2] ),
								 2.0 * ( S_inv(2,2)*diff[2] + S_inv(0,2)*diff[0] + S_inv(1,2)*diff[1] ) };

	var = 0.0;
	for(Int4 i=0; i<PNLatConst(); i++)
	{
		for(Int4 j=0; j<i; j++)
		{
			var += (coef_lat[i]*coef_lat[j]+coef_lat[j]*coef_lat[i])*S_inv_covar(i,j);
		}
		var+=coef_lat[i]*coef_lat[i]*S_inv_covar(i,i);
	}
	
	for(Int4 i=0, i2=PNXYZ(); i<PNXYZ(); i++, i2++)
	{
		for(Int4 j=0, j2=PNXYZ(); j<i; j++, j2++)
		{
			var += ( coef_pos[i]*coef_pos[j] + coef_pos[j]*coef_pos[i] )
						*( atom_pos_covar(i,j) + atom_pos_covar(i2,j2) - ( atom_pos_covar(i,j2) + atom_pos_covar(i2,j) ) );
		}
		var += coef_pos[i]*coef_pos[i]*( atom_pos_covar(i,i) + atom_pos_covar(i2,i2) - 2.0 * atom_pos_covar(i,i2) );
	}
	
	return norm(diff, S_inv);
}

Double calBondAngle(const VecDat3<Double>& xyz1,
					const VecDat3<Double>& xyz2, const VecDat3<Double>& xyz3, const SymMat3<Double>& S_inv)
{
	const VecDat3<Double> diff12 = xyz1 - xyz2;
	const VecDat3<Double> diff32 = xyz3 - xyz2;
	const Double inn = inner_product(diff12, diff32, S_inv);
	const Double len12_32 = sqrt( norm(diff12, S_inv) * norm(diff32, S_inv) );
	if( len12_32 <= 0.0 ) throw nerror(ZRTErrorTypeZeroDivision, __FILE__, __LINE__, __FUNCTION__ );
	
	const Double cos_angle = inn / len12_32;
	const Double t = 1.0 - cos_angle*cos_angle;
	
	if( t <= 0.0 )
	{
		if( inn > 0.0 ) return 0.0;
		else return ZRTConstantPi;
	}
	else
	{
		return atan2( sqrt( t ), cos_angle );
	}
}

// On input, the argument atom_pos_covar is the covariant matrix on xyz1, xyz2 and xyz3.
// (Hence, it is a square matrix of size 9.)
Double calBondAngle(const VecDat3<Double>& xyz1,
		const VecDat3<Double>& xyz2, const VecDat3<Double>& xyz3, const SymMat3<Double>& S_inv,
		const SymMat<Double>& S_inv_covar, const SymMat<Double>& atom_pos_covar, Double& var)
{
	const VecDat3<Double> diff12 = xyz1 - xyz2;
	const VecDat3<Double> diff32 = xyz3 - xyz2;

	const Double inn = inner_product(diff12, diff32, S_inv);
	const Double norm12 = norm(diff12, S_inv);
	const Double norm32 = norm(diff32, S_inv);

	const Double len12_32 = sqrt( norm12 * norm32 );
	if( len12_32 <= 0.0 ) throw nerror(ZRTErrorTypeZeroDivision, __FILE__, __LINE__, __FUNCTION__ );

	const Double cos_angle = inn / len12_32;
	const Double t = 1.0 - cos_angle*cos_angle;

	if( t <= 0.0 )
	{
		var = 0.0;
		if( inn > 0.0 ) return 0.0;
		else return ZRTConstantPi;
	}
	else
	{
		// dcos_angle = ( dinn - 0.5 * inn * (dnorm32 / norm32  + dnorm12 / norm12) ) / len12_32
		//            = ( dinn / inn - 0.5 * ( dnorm12 / norm12 + dnorm32 / norm32) ) * cos_angle.
		const Double coef_lat[6] = {diff12[0]*diff32[0] - 0.5*inn*( diff12[0]*diff12[0] / norm12 + diff32[0]*diff32[0] / norm32 ),
									diff12[1]*diff32[1] - 0.5*inn*( diff12[1]*diff12[1] / norm12 + diff32[1]*diff32[1] / norm32 ),
									diff12[2]*diff32[2] - 0.5*inn*( diff12[2]*diff12[2] / norm12 + diff32[2]*diff32[2] / norm32 ),
									2.0*diff12[1]*diff32[2] - inn*( diff12[1]*diff12[2] / norm12 + diff32[1]*diff32[2] / norm32 ),
									2.0*diff12[0]*diff32[2] - inn*( diff12[0]*diff12[2] / norm12 + diff32[0]*diff32[2] / norm32 ),
									2.0*diff12[0]*diff32[1] - inn*( diff12[0]*diff12[1] / norm12 + diff32[0]*diff32[1] / norm32 ) };
		
		Double coef_pos[9];
		coef_pos[0] = S_inv(0,0)*diff32[0] + 2.0 * ( S_inv(0,1)*diff32[1] + S_inv(0,2)*diff32[2] )
									- inn * ( S_inv(0,0)*diff12[0] + S_inv(0,1)*diff12[1] + S_inv(0,2)*diff12[2] ) / norm12;
		coef_pos[1] = S_inv(1,1)*diff32[1] + 2.0 * ( S_inv(0,1)*diff32[0] + S_inv(1,2)*diff32[2] )
								  	- inn * ( S_inv(1,1)*diff12[1] + S_inv(0,1)*diff12[0] + S_inv(1,2)*diff12[2] ) / norm12;
		coef_pos[2] = S_inv(2,2)*diff32[2] + 2.0 * ( S_inv(0,2)*diff32[0] + S_inv(1,2)*diff32[1] )
		 							- inn * ( S_inv(2,2)*diff12[2] + S_inv(0,2)*diff12[0] + S_inv(1,2)*diff12[1] ) / norm12;

		coef_pos[6] = S_inv(0,0)*diff12[0] + 2.0 * ( S_inv(0,1)*diff12[1] + S_inv(0,2)*diff12[2] )
									- inn * ( S_inv(0,0)*diff32[0] + S_inv(0,1)*diff32[1] + S_inv(0,2)*diff32[2] ) / norm32;
		coef_pos[7] = S_inv(1,1)*diff12[1] + 2.0 * ( S_inv(0,1)*diff12[0] + S_inv(1,2)*diff12[2] )
								  	- inn * ( S_inv(1,1)*diff32[1] + S_inv(0,1)*diff32[0] + S_inv(1,2)*diff32[2] ) / norm32;
		coef_pos[8] = S_inv(2,2)*diff12[2] + 2.0 * ( S_inv(0,2)*diff12[0] + S_inv(1,2)*diff12[1] )
									- inn * ( S_inv(2,2)*diff32[2] + S_inv(0,2)*diff32[0] + S_inv(1,2)*diff32[1] ) / norm32;

		coef_pos[3] = - (coef_pos[0] + coef_pos[6]);
		coef_pos[4] = - (coef_pos[1] + coef_pos[7]);
		coef_pos[5] = - (coef_pos[2] + coef_pos[8]);

		var = 0.0;
		for(Int4 i=0; i<PNLatConst(); i++)
		{
			for(Int4 j=0; j<i; j++)
			{
				var += (coef_lat[i]*coef_lat[j]+coef_lat[j]*coef_lat[i])*S_inv_covar(i,j);
			}
			var+=coef_lat[i]*coef_lat[i]*S_inv_covar(i,i);
		}
		for(Int4 i=0; i<PNXYZ()*3; i++)
		{
			for(Int4 j=0; j<i; j++)
			{
				var += ( coef_pos[i]*coef_pos[j] + coef_pos[j]*coef_pos[i] ) * atom_pos_covar(i,j);
			}
			var += coef_pos[i]*coef_pos[i]*atom_pos_covar(i,i);
		}
		var /= norm12*norm32*t;
		return atan2( sqrt( t ), cos_angle );
	}
}


Double calAtomDistance(const VecDat3<Double>& xyz1, const VecDat3<Double>& xyz2, const SymMat3<Double>& S_inv)
{
	return sqrt( norm(xyz1 - xyz2, S_inv) );
}


Double calAtomDistance(
const VecDat3<Double>& xyz1, const VecDat3<Double>& xyz2, const SymMat3<Double>& S_inv,
VecDat3<Double>& ddist_dxyz1, VecDat3<Double>& ddist_dxyz2)
{
	const VecDat3<Double> diff = xyz1 - xyz2;
	const Double dist = sqrt( norm(diff, S_inv) );
	if(dist == 0.0) throw nerror(ZRTErrorTypeZeroDivision, __FILE__, __LINE__, __FUNCTION__);

	ddist_dxyz1 = symmetric_transform(S_inv, diff);

	Double inv_dist = 1.0/dist;
	ddist_dxyz1 *= inv_dist;
	ddist_dxyz2 = ddist_dxyz1 * (-1.0);

	return dist;
}


Double calAtomDistance(
const VecDat3<Double>& xyz1, const VecDat3<Double>& xyz2, const SymMat3<Double>& S_inv,
VecDat3<Double>& ddist_dxyz1, VecDat3<Double>& ddist_dxyz2, SymMat3<Double>& ddist_dS)
{
	const VecDat3<Double> diff = xyz1 - xyz2;
	const Double dist = sqrt( norm(diff, S_inv) );
	if(dist == 0.0) throw nerror(ZRTErrorTypeZeroDivision, __FILE__, __LINE__, __FUNCTION__);

	ddist_dxyz1 = symmetric_transform(S_inv, diff);
	ddist_dS = product_self_transpose( symmetric_transform(S_inv, diff) );

	Double inv_dist = 1.0/dist;
	ddist_dxyz1 *= inv_dist;
	ddist_dxyz2 = ddist_dxyz1 * (-1.0);
	ddist_dS *= -0.5*inv_dist;
	return dist;
}


Double calBondAngle(
const VecDat3<Double>& xyz1, const VecDat3<Double>& xyz2, const VecDat3<Double>& xyz3, const SymMat3<Double>& S_inv,
VecDat3<Double>& ddist_dxyz1, VecDat3<Double>& ddist_dxyz2, VecDat3<Double>& ddist_dxyz3)
{
	VecDat3<Double> diff12 = xyz1 - xyz2;
	VecDat3<Double> diff32 = xyz3 - xyz2;
	
	const Double dist12_invsq = 1.0/norm(diff12, S_inv);
	const Double dist32_invsq = 1.0/norm(diff32, S_inv);
	const Double innerprod_123 = inner_product(diff12, diff32, S_inv);

	const Double dist12_inv = sqrt( dist12_invsq );
	const Double dist32_inv = sqrt( dist32_invsq );

	const Double cos_angle = innerprod_123 * dist12_inv * dist32_inv;
	const Double sin_angle = sqrt( 1.0 - cos_angle*cos_angle );
	
	diff12 = symmetric_transform(S_inv, diff12);
	diff32 = symmetric_transform(S_inv, diff32);

	ddist_dxyz1 = diff12 + diff32 * (-innerprod_123 * dist32_invsq);
	ddist_dxyz2 = ( diff12 + diff32 ) * ( 1.0 - innerprod_123 * (dist32_invsq + dist12_invsq) ) ;
	ddist_dxyz3 = diff32 + diff12 * (-innerprod_123 * dist12_invsq);

	Double t = -dist12_inv * dist32_inv / sin_angle;
	ddist_dxyz1 *= t;
	ddist_dxyz2 *= t;
	ddist_dxyz3 *= t;

	return atan2( sin_angle, cos_angle );
}


Double calBondAngle(
const VecDat3<Double>& xyz1, const VecDat3<Double>& xyz2, const VecDat3<Double>& xyz3, const SymMat3<Double>& S_inv,
VecDat3<Double>& ddist_dxyz1, VecDat3<Double>& ddist_dxyz2, VecDat3<Double>& ddist_dxyz3, SymMat3<Double>& ddist_dS)
{
	VecDat3<Double> diff12 = xyz1 - xyz2;
	VecDat3<Double> diff32 = xyz3 - xyz2;
	
	const Double dist12_invsq = 1.0/norm(diff12, S_inv);
	const Double dist32_invsq = 1.0/norm(diff32, S_inv);
	const Double innerprod_123 = inner_product(diff12, diff32, S_inv);

	const Double dist12_inv = sqrt( dist12_invsq );
	const Double dist32_inv = sqrt( dist32_invsq );

	const Double cos_angle = innerprod_123 * dist12_inv * dist32_inv;
	const Double sin_angle = sqrt( 1.0 - cos_angle*cos_angle );
	
	diff12 = symmetric_transform(S_inv, diff12);
	diff32 = symmetric_transform(S_inv, diff32);

	ddist_dxyz2 = ( diff12 + diff32 ) * ( 1.0 - innerprod_123 * (dist32_invsq + dist12_invsq) ) ;
	ddist_dxyz1 = diff32 * (-innerprod_123 * dist32_invsq);
	ddist_dxyz3 = diff12 * (-innerprod_123 * dist12_invsq);

	Double s = sin_angle + 1.0;
	Double t = 1.0 / s;
	ddist_dS = product_transpose( diff12 + ddist_dxyz1 * t, diff32 + ddist_dxyz3 * t ) * (-0.5 * s);

	ddist_dxyz1 += diff12;
	ddist_dxyz3 += diff32;
	
	t = -dist12_inv * dist32_inv / sin_angle;
	ddist_dxyz1 *= t;
	ddist_dxyz2 *= t;
	ddist_dxyz3 *= t;
	ddist_dS *= t;

	return atan2( sin_angle, cos_angle );
}
