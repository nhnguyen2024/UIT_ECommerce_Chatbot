// Where the backend lives. Empty means same origin, which is how local
// development works: the Angular dev server proxies /api to the backend.
// infra/deploy-frontend.sh overwrites this file with the deployed backend URL,
// so one build serves every environment.
window.NORTHLIGHT_API_BASE = '';
