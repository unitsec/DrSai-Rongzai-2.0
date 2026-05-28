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

# 只使用提示字典中已有的语言来构建检测器
detector = LanguageDetectorBuilder.from_languages(*LANG_PROMPTS.keys()).build()


def generate_prompt(text: str) -> str:
    if not text.strip():
        return "请提供一些文本。"
    lang = detector.detect_language_of(text)
    if lang is None:
        return "无法识别语言，请尝试更长的句子。"
    return LANG_PROMPTS.get(
        lang,
        f"Please continue to work with using English."
    )