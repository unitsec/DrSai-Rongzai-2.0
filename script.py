from lingua import LanguageDetectorBuilder
detector = LanguageDetectorBuilder.from_all_languages().build()
print(detector.detect_language_of("你好，帮我处理group5的Si数据"))
