import React, { useContext, useEffect, useState } from "react";
import { useLocation, useNavigate } from "../hooks/useRouter";
import { appContext } from "../hooks/provider";
import { verifyAuthSession, saveAuthSession } from "../utils/authSession";
import { ENABLE_SSO } from "../utils/authConfig";
import { authAPI } from "../components/views/api";
import ScienceUserErrorPage from "./ScienceUserErrorPage";

const PUBLIC_ROUTES = ["/login", "/auth", "/share"];

const normalizePath = (path: string) => path.replace(/\/{2,}/g, "/").replace(/\/$/, "") || "/";

interface RouteGuardProps {
    children: React.ReactNode;
}

export const RouteGuard: React.FC<RouteGuardProps> = ({ children }) => {
    const location = useLocation();
    const navigate = useNavigate();
    const { setUser } = useContext(appContext);
    const [checked, setChecked] = useState(false);
    const [scienceAuthError, setScienceAuthError] = useState<"invalidToken" | "networkError" | "missingToken" | null>(null);

    useEffect(() => {
        let cancelled = false;

        const guard = async () => {
            const normalizedPath = normalizePath(location.pathname);
            const searchParams = new URLSearchParams(location.search);

            if (ENABLE_SSO && searchParams.get("user_source") === "science_user") {
                const accessToken = searchParams.get("access_token");
                const username = searchParams.get("username");
                const tokenId = searchParams.get("tokenId");
                try {
                    const result = accessToken && username
                        ? await authAPI.scienceUserVerify(accessToken, username)
                        : tokenId ? await authAPI.scienceUserLogin(tokenId) : null;
                    if (!result) {
                        setScienceAuthError("missingToken");
                        return;
                    }
                    if (cancelled) return;
                    saveAuthSession(result.access_token, result.user_id);
                    localStorage.removeItem("drsai-mode-config");
                    localStorage.removeItem("drsai.recentAgents");
                    setUser({ name: result.user_id, email: result.user_id });
                    window.location.replace("/?menu=current_session&view=chat");
                } catch (err: any) {
                    if (!cancelled) {
                        const isNetwork = err instanceof TypeError || String(err?.message).includes("fetch");
                        setScienceAuthError(isNetwork ? "networkError" : "invalidToken");
                    }
                }
                return;
            }

            const isPublicRoute = PUBLIC_ROUTES.some(
                (route) => normalizePath(route) === normalizedPath
            );
            const isLogout = ENABLE_SSO && searchParams.get("logout") === "1";

            if (isPublicRoute) {
                if (normalizedPath === "/login" && !isLogout) {
                    const session = await verifyAuthSession();
                    if (!cancelled && session.ok) {
                        setUser({ email: session.userEmail, name: session.userEmail });
                        navigate("/", { replace: true });
                        return;
                    }
                }
                if (!cancelled) {
                    setChecked(true);
                }
                return;
            }

            if (isLogout) {
                if (!cancelled) navigate("/login?logout=1", { replace: true });
                return;
            }

            const session = await verifyAuthSession();
            if (cancelled) {
                return;
            }

            if (session.ok) {
                setUser({ email: session.userEmail, name: session.userEmail });
                setChecked(true);
                return;
            }

            if (normalizedPath !== normalizePath("/login")) {
                navigate(ENABLE_SSO ? "/login?logout=1" : "/login", { replace: true });
            }
        };

        setChecked(false);
        void guard();

        return () => {
            cancelled = true;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [location.pathname]);

    if (ENABLE_SSO && scienceAuthError) {
        return <ScienceUserErrorPage errorType={scienceAuthError} />;
    }

    if (ENABLE_SSO && new URLSearchParams(location.search).get("user_source") === "science_user") {
        return null;
    }

    if (!checked) {
        return null;
    }

    return <>{children}</>;
};
