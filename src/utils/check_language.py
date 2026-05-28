from lingua import LanguageDetectorBuilder, Language

# 先定义提示语字典
LANG_PROMPTS = {
    Language.CHINESE: '后面请你持续使用中文工作。',
    Language.JAPANESE: 'これからは日本語で作業を続けてください。',
    Language.KOREAN: '이제부터 한국어로 계속 작업해 주세요.',
    Language.THAI: 'กรุณาทำงานเป็นภาษาไทยต่อไป',
    Language.VIETNAMESE: 'Vui lòng tiếp tục làm việc bằng tiếng Việt.',
    Language.INDONESIAN: 'Silakan terus bekerja dalam bahasa Indonesia.',
    Language.MALAY: 'Sila terus bekerja dalam bahasa Melayu.',
    Language.HINDI: 'कृपया हिंदी में काम करना जारी रखें।',
    Language.ARABIC: 'الرجاء الاستمرار في العمل باللغة العربية.',
    Language.HEBREW: 'אנא המשך לעבוד בעברית.',
    Language.ENGLISH: 'Please continue to work in English from now on.',
    Language.GERMAN: 'Bitte arbeiten Sie weiterhin auf Deutsch.',
    Language.DUTCH: 'Blijf alstublieft in het Nederlands werken.',
    Language.SWEDISH: 'Vänligen fortsätt att arbeta på svenska.',
    Language.DANISH: 'Fortsæt venligst med at arbejde på dansk.',
    Language.FRENCH: 'Veuillez continuer à travailler en français.',
    Language.SPANISH: 'Por favor, continúa trabajando en español.',
    Language.RUSSIAN: 'Пожалуйста, продолжайте работать на русском языке.',
}

# 置信度领先阈值：第一名需要比第二名高出30%
CONFIDENCE_LEAD_THRESHOLD = 0.30

# 只使用提示字典中已有的语言来构建检测器
detector = LanguageDetectorBuilder.from_languages(*LANG_PROMPTS.keys()).build()


def generate_prompt(text: str) -> str:
    if not text.strip():
        return "请提供一些文本。"
    
    try:
        # 获取置信度值
        confidence_values = detector.compute_language_confidence_values(text)
        
        # 立即转换为 Python float，避免 numpy.float16 问题
        top1 = float(confidence_values[0].value)
        top2 = float(confidence_values[1].value) if len(confidence_values) > 1 else 0.0
        
        lang = confidence_values[0].language
        
        # 如果第一名没有比第二名高出30%，直接用English
        if (top1 - top2) < CONFIDENCE_LEAD_THRESHOLD:
            return LANG_PROMPTS[Language.ENGLISH]
        
        return LANG_PROMPTS.get(lang, LANG_PROMPTS[Language.ENGLISH])
        
    except Exception as e:
        # 出现异常时降级处理
        print(f"Confidence calculation failed: {e}")
        lang = detector.detect_language_of(text)
        if lang is None:
            return LANG_PROMPTS[Language.ENGLISH]
        return LANG_PROMPTS.get(lang, LANG_PROMPTS[Language.ENGLISH])


# 测试函数
def test_confidence(text):
    print(f"\n测试文本: '{text[:50]}{'...' if len(text) > 50 else ''}'")
    print("=" * 60)
    
    try:
        confidence_values = detector.compute_language_confidence_values(text)
        
        print("Top 5 语言置信度：")
        print("-" * 40)
        for i, result in enumerate(confidence_values[:5], 1):
            lang_name = result.language.name
            confidence = float(result.value)  # 转换避免错误
            print(f"{i:2d}. {lang_name:15s} : {confidence:.4f} ({confidence*100:.2f}%)")
        
        # 计算领先差距
        top1 = float(confidence_values[0].value)
        top2 = float(confidence_values[1].value) if len(confidence_values) > 1 else 0.0
        lead = top1 - top2
        
        print("-" * 40)
        print(f"领先差距: {lead*100:.2f}% (要求: {CONFIDENCE_LEAD_THRESHOLD*100}%)")
        
        # 测试 generate_prompt 函数
        prompt = generate_prompt(text)
        print(f"最终选择: {confidence_values[0].language.name if lead >= CONFIDENCE_LEAD_THRESHOLD else 'ENGLISH (默认)'}")
        print(f"返回提示: {prompt[:50]}{'...' if len(prompt) > 50 else ''}")
            
    except Exception as e:
        print(f"错误: {type(e).__name__}: {e}")
        # 降级处理
        lang = detector.detect_language_of(text)
        if lang:
            print(f"降级检测: {lang.name}")
        else:
            print("降级检测: 无法识别")


# 测试你提到的长文本
if __name__ == "__main__":
    test_text = """from lingua import LanguageDetectorBuilder, Language

# 先定义提示语字典
LANG_PROMPTS = {
    Language.CHINESE: '后面请你持续使用中文工作。',
    Language.JAPANESE: 'これからは日本語で作業を続けてください。',
}"""
    
    test_confidence(test_text)
    
    # 测试其他类型
    test_confidence("hello world")
    test_confidence("你好世界")
    test_confidence("bonjour le monde")