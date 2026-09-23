import * as React from "react";
import { BookOpenText } from "lucide-react";
import { useLang } from "../../i18n/useLang";

interface CitationNoticeProps {
  compact?: boolean;
}

const CITATION_COPY = {
  zh: {
    paperIntro: "如果Rongzai智能体对您的数据分析有帮助，请在论文中引用我们的方法学论文：",
    thanksIntro: "同时，如果您愿意，可以在您的成果致谢中提及本智能体：",
  },
  en: {
    paperIntro: "If the Rongzai agent has been helpful for your data analysis, please cite our methodology paper:",
    thanksIntro: "We also recommend, but do not require, acknowledging this agent in your work:",
  },
} as const;

const PAPER_TITLE =
  "Rongzai agent: A Large Language Model-Based Autonomous Assistant for Rietveld Refinement of Neutron Diffraction Data";
const PAPER_URL = "https://arxiv.org/abs/2605.13911";

const ACKNOWLEDGEMENT_TEMPLATE =
  "We thank the data analysis group at CSNS for providing the DrSai-Rongzai agent to assist neutron scattering data analysis, which facilitated our data interpretation.";

export default function CitationNotice({ compact = false }: CitationNoticeProps) {
  const { lang } = useLang();
  const copy = CITATION_COPY[lang];

  return (
    <aside
      className={`mx-auto w-full max-w-3xl rounded-xl border border-border-primary/55 bg-white/72 text-left shadow-sm backdrop-blur-md dark:bg-white/[0.045] ${compact ? "px-3 py-2.5" : "px-4 py-3 sm:px-5"}`}
      aria-label="DrSai-Rongzai citation notice"
    >
      <div className="flex gap-3">
        <div className="mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-accent/10 text-accent ring-1 ring-inset ring-accent/15">
          <BookOpenText className="h-4 w-4" aria-hidden />
        </div>
        <div className="min-w-0 flex-1 space-y-2 text-[12px] leading-relaxed text-secondary sm:text-[13px]">
          <p>
            {copy.paperIntro}
            <span className="mt-1 block font-agent-mono text-[11px] leading-relaxed text-primary/85 sm:text-xs">
              Li Q, Wang H, Xiong D, et al. {" "}
              <a
                href={PAPER_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="underline decoration-accent/45 underline-offset-2 transition-colors hover:text-accent"
              >
                {PAPER_TITLE}
              </a>
              [J]. arXiv preprint arXiv:2605.13911, 2026.
            </span>
          </p>
          <p>
            {copy.thanksIntro}
            <span className="mt-1 block border-l-2 border-accent/30 pl-3 text-primary/80">
              {ACKNOWLEDGEMENT_TEMPLATE}
            </span>
          </p>
        </div>
      </div>
    </aside>
  );
}