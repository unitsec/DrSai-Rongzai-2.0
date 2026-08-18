import React from "react";
import { useLang } from "../i18n/useLang";

type ErrorType = "invalidToken" | "networkError" | "missingToken";

interface Props {
    errorType?: ErrorType;
}

const ScienceUserErrorPage: React.FC<Props> = ({ errorType = "invalidToken" }) => {
    const { t } = useLang();
    const msgKey = `scienceAuth.error.${errorType}` as const;

    return (
        <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-slate-950 px-4">
            <div className="w-full max-w-md bg-white dark:bg-slate-900 rounded-xl shadow-lg p-8 text-center">
                <h1 className="text-xl font-bold text-gray-900 dark:text-white mb-3">
                    {t("scienceAuth.error.title")}
                </h1>
                <p className="text-sm text-gray-600 dark:text-slate-400 mb-8 leading-relaxed">
                    {t(msgKey)}
                </p>
                <div className="flex gap-3 justify-center">
                    <button type="button" onClick={() => window.location.reload()} className="px-5 py-2 text-sm font-semibold text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors">
                        {t("scienceAuth.error.retry")}
                    </button>
                    <button type="button" onClick={() => window.location.href = "mailto:support@ihep.ac.cn"} className="px-5 py-2 text-sm font-semibold text-gray-700 dark:text-slate-300 bg-gray-100 dark:bg-slate-800 hover:bg-gray-200 dark:hover:bg-slate-700 rounded-lg transition-colors">
                        {t("scienceAuth.error.contact")}
                    </button>
                </div>
            </div>
        </div>
    );
};

export default ScienceUserErrorPage;