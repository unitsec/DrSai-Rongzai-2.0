/** SERVICE_MODE is injected by Gatsby at build time from the repository .env. */
export const ENABLE_SSO = process.env.GATSBY_SERVICE_MODE === "PROD";
export const ENABLE_LOCAL_REGISTRATION = !ENABLE_SSO;