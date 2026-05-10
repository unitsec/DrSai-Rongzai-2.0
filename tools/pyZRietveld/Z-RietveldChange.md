# Fronter
Because the code of Z-Rietveld doesn't adapt the Linux as well as other problem, I changed some code to adjust it to our project. The changed code would be listed below.

# ZRTLibFolder.cc
line 17-22: added the head file that Linux needs（unistd.h、limits.h、cstdlib、     filesystem etc）
line 241-285: realized the GetLibFolderPath branch of Linux