import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import csv
import os

csvfile = sys.argv[1]
pngfile = sys.argv[2]

xs = []
ys = []
yc = []
with open(csvfile,'r') as f:
    r = csv.reader(f)
    hdr = next(r,None)
    for row in r:
        try:
            xs.append(float(row[0])); ys.append(float(row[1])); yc.append(float(row[2]))
        except:
            pass

res = [ (y - c) for y,c in zip(ys,yc) ]

fig, (ax1, ax2) = plt.subplots(2,1, figsize=(8,6), gridspec_kw={'height_ratios':[3,1]}, sharex=True)
ax1.plot(xs, ys, '-', label='Observed', lw=0.8)
ax1.plot(xs, yc, '-', label='Calculated', lw=0.8)
ax1.legend()
ax1.set_ylabel('Intensity')
ax1.set_title(os.path.basename(csvfile))

ax2.plot(xs, res, '-', lw=0.6)
ax2.axhline(0, color='k', lw=0.4)
ax2.set_xlabel('X')
ax2.set_ylabel('Residual')

plt.tight_layout()
plt.savefig(pngfile, dpi=150)
print('Wrote', pngfile)
