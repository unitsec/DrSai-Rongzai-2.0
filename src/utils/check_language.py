from lingua import LanguageDetectorBuilder, Language

detector = LanguageDetectorBuilder.from_all_languages().build()

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
    Language.ICELANDIC: 'Vinsamlegast haltu áfram að vinna á íslensku.',
    Language.FRENCH: 'Veuillez continuer à travailler en français.',
    Language.SPANISH: 'Por favor, continúa trabajando en español.',
    Language.PORTUGUESE: 'Por favor, continue a trabalhar em português.',
    Language.ITALIAN: 'Per favore, continua a lavorare in italiano.',
    Language.ROMANIAN: 'Vă rugăm să continuați să lucrați în limba română.',
    Language.CATALAN: 'Si us plau, continueu treballant en català.',
    Language.RUSSIAN: 'Пожалуйста, продолжайте работать на русском языке.',
    Language.POLISH: 'Proszę kontynuować pracę w języku polskim.',
    Language.CZECH: 'Pokračujte prosím v práci v češtině.',
    Language.SLOVAK: 'Pokračujte prosím v práci v slovenčine.',
    Language.UKRAINIAN: 'Будь ласка, продовжуйте працювати українською.',
    Language.BULGARIAN: 'Моля, продължете да работите на български.',
    Language.SERBIAN: 'Молимо вас да наставите да радите на српском.',
    Language.CROATIAN: 'Molimo nastavite raditi na hrvatskom.',
    Language.SLOVENE: 'Prosimo, nadaljujte z delom v slovenščini.',
    Language.LITHUANIAN: 'Prašome toliau dirbti lietuviškai.',
    Language.LATVIAN: 'Lūdzu, turpiniet strādāt latviski.',
    Language.FINNISH: 'Jatkathan työskentelyä suomeksi.',
    Language.ESTONIAN: 'Palun jätkake eesti keeles.',
    Language.HUNGARIAN: 'Kérjük, folytassa a munkát magyarul.',
    Language.GREEK: 'Παρακαλώ συνεχίστε να εργάζεστε στα ελληνικά.',
    Language.TURKISH: 'Lütfen Türkçe çalışmaya devam edin.',
    Language.BASQUE: 'Jarrai ezazu euskaraz lanean, mesedez.',
    Language.WELSH: 'Parhewch i weithio yn Gymraeg, os gwelwch yn dda.',
}


def generate_prompt(text: str) -> str:
    if not text.strip():
        return "请提供一些文本。"
    lang = detector.detect_language_of(text)
    if lang is None:
        return "无法识别语言，请尝试更长的句子。"
    return LANG_PROMPTS.get(
        lang,
        f"Please continue to work in {lang.name.lower()}."
    )

# 测试句子字典 —— 每个语言一句日常用语
LANG_TESTS = {
    Language.CHINESE:            "你好，今天天气真好。",
    Language.JAPANESE:           "こんにちは、今日はいい天気ですね。",
    Language.KOREAN:             "안녕하세요, 오늘 날씨가 정말 좋네요.",
    Language.THAI:               "สวัสดีครับ วันนี้อากาศดีมาก",
    Language.VIETNAMESE:         "Xin chào, hôm nay thời tiết đẹp quá.",
    Language.INDONESIAN:         "Halo, cuaca hari ini sangat bagus.",
    Language.MALAY:              "Helo, cuaca hari ini sangat baik.",
    Language.HINDI:              "नमस्ते, आज मौसम बहुत अच्छा है।",
    Language.ARABIC:             "مرحباً، الطقس اليوم جميل جداً.",
    Language.HEBREW:             "שלום, היום מזג האוויר נהדר.",
    Language.ENGLISH:            "Hello, how are you today?",
    Language.GERMAN:             "Hallo, wie geht es Ihnen heute?",
    Language.DUTCH:              "Hallo, hoe gaat het met u vandaag?",
    Language.SWEDISH:            "Hej, hur mår du idag?",
    Language.DANISH:             "Hej, hvordan har du det i dag?",
    Language.ICELANDIC:          "Halló, hvernig hefurðu það í dag?",
    Language.FRENCH:             "Bonjour, comment allez-vous aujourd'hui ?",
    Language.SPANISH:            "Hola, ¿cómo estás hoy?",
    Language.PORTUGUESE:         "Olá, como está hoje?",
    Language.ITALIAN:            "Ciao, come stai oggi?",
    Language.ROMANIAN:           "Bună ziua, ce mai faceți astăzi?",
    Language.CATALAN:            "Hola, com estàs avui?",
    Language.RUSSIAN:            "Здравствуйте, как у вас дела сегодня?",
    Language.POLISH:             "Cześć, jak się masz dzisiaj?",
    Language.CZECH:              "Ahoj, jak se máš dnes?",
    Language.SLOVAK:             "Ahoj, ako sa máš dnes?",
    Language.UKRAINIAN:          "Привіт, як справи сьогодні?",
    Language.BULGARIAN:          "Здравейте, как сте днес?",
    Language.SERBIAN:            "Здраво, како сте данас?",
    Language.CROATIAN:           "Bok, kako ste danas?",
    Language.SLOVENE:            "Živjo, kako ste danes?",
    Language.LITHUANIAN:         "Sveiki, kaip šiandien sekasi?",
    Language.LATVIAN:            "Sveiki, kā jums šodien klājas?",
    Language.FINNISH:            "Hei, mitä kuuluu tänään?",
    Language.ESTONIAN:           "Tere, kuidas teil täna läheb?",
    Language.HUNGARIAN:          "Helló, hogy van ma?",
    Language.GREEK:              "Γεια σας, πώς είστε σήμερα;",
    Language.TURKISH:            "Merhaba, bugün nasılsınız?",
    Language.BASQUE:             "Kaixo, zer moduz zaude gaur?",
    Language.WELSH:              "Helo, sut ydych chi heddiw?",
    # 如果你也加上了挪威语：
    # Language.BOKMAL:           "Hei, hvordan går det i dag?",
}

if __name__ == '__main__':
    print("=== 语言检测测试 ===")
    for lang, test_text in LANG_TESTS.items():
        detected = detector.detect_language_of(test_text)
        # 获取母语提示
        prompt = LANG_PROMPTS.get(detected, f"Please continue to work in {detected.name.lower()}.")
        # 输出原文、检测到的语言、以及最终提示
        print(f"[{lang.name:20}] 输入: {test_text}")
        print(f"            检测为: {detected.name:20} → {prompt}")
        print("---")