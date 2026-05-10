■libomp 18.1.8の入手方法  
LLVMプロジェクトのソースコードがからビルドを試みたところ、cmakeで、いくつかエラーが出てきたため  
Homebrewにて、入手しました  
brew install libomp  
includeフォルダとlibomp.dylibを入手  

Homebrewにて、普通にインストールすると、Universal Binaryになっていないため  
Apple Silicon搭載機で、ARM版を入手し、Intel CPU搭載機で、x86_64版を入手  
lipoコマンドを使って、Universal Binary化しました  
lipo -create ARM/libomp.dylib Intel/libomp.dylib -output libomp.dylib  

■libomp 12以前の入手方法  
このリポジトリに置いてあるlibomp.dylib (macOS用)は、https://github.com/Z-Rietveld/OpenMP_Source_Code に置いたソースコードから作成したものです
