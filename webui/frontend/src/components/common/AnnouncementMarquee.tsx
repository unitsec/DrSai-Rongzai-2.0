import * as React from "react";
import { X } from "lucide-react";
import { useLang } from "../../i18n/useLang";
import { parseAgentText } from "../../utils/agentLocalizedText";
import type { AgentExample } from "../../types/common";

interface AnnouncementMarqueeProps {
  fallbackAnnouncements?: AgentExample[];
  className?: string;
}

function normalizeAnnouncements(payload: unknown): AgentExample[] {
  const raw = Array.isArray(payload)
    ? payload
    : payload && typeof payload === "object" && Array.isArray((payload as { announcements?: unknown }).announcements)
      ? (payload as { announcements: unknown[] }).announcements
      : [];

  return raw.filter((item): item is AgentExample => {
    if (typeof item === "string") return item.trim().length > 0;
    return Boolean(
      item &&
      typeof item === "object" &&
      (typeof (item as { zh?: unknown }).zh === "string" ||
        typeof (item as { en?: unknown }).en === "string")
    );
  });
}

export default function AnnouncementMarquee({
  fallbackAnnouncements = [],
  className = "",
}: AnnouncementMarqueeProps) {
  const { lang } = useLang();
  const [showAnnouncement, setShowAnnouncement] = React.useState(true);
  const [liveAnnouncements, setLiveAnnouncements] = React.useState<AgentExample[]>([]);
  const announcements = liveAnnouncements.length > 0 ? liveAnnouncements : fallbackAnnouncements;
  const announcementText = React.useMemo(
    () => announcements.map((item) => parseAgentText(item, lang)).filter(Boolean).join("  ·  "),
    [announcements, lang]
  );

  React.useEffect(() => {
    let cancelled = false;

    const loadAnnouncements = async () => {
      try {
        const response = await fetch(`/announcements.json?t=${Date.now()}`, { cache: "no-store" });
        if (!response.ok) return;
        const payload = await response.json();
        if (!cancelled) {
          setLiveAnnouncements(normalizeAnnouncements(payload));
        }
      } catch {
        if (!cancelled) {
          setLiveAnnouncements([]);
        }
      }
    };

    void loadAnnouncements();
    const timer = window.setInterval(loadAnnouncements, 30000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  React.useEffect(() => {
    if (announcementText) {
      setShowAnnouncement(true);
    }
  }, [announcementText]);

  if (!showAnnouncement || !announcementText) return null;

  return (
    <div className={`relative z-10 mx-auto flex w-[min(52rem,calc(100%-2rem))] items-center gap-2 overflow-hidden rounded-full border border-border-primary/55 bg-white/72 px-3 py-1.5 shadow-sm backdrop-blur-md dark:bg-white/[0.045] ${className}`}>
      <div className="min-w-0 flex-1 overflow-hidden [mask-image:linear-gradient(90deg,transparent,black_10%,black_90%,transparent)]">
        <div
          className="font-agent-mono animate-marquee-x flex w-max whitespace-nowrap text-[10px] font-medium text-accent sm:text-[11px]"
          style={{ "--marquee-duration": "22s" } as React.CSSProperties}
        >
          <span className="pr-10">{announcementText}</span>
          <span className="pr-10" aria-hidden>{announcementText}</span>
        </div>
      </div>
      <button
        type="button"
        onClick={() => setShowAnnouncement(false)}
        className="shrink-0 rounded-full p-1 text-accent/60 transition hover:bg-accent/10 hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        aria-label="关闭公告"
      >
        <X className="h-3.5 w-3.5" aria-hidden />
      </button>
    </div>
  );
}