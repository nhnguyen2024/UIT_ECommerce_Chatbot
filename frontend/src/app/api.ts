import { HttpInterceptorFn } from '@angular/common/http';

declare global {
  interface Window {
    NORTHLIGHT_API_BASE?: string;
  }
}

/** Resolve an `/api/...` path against the configured backend (see public/config.js). */
export function apiUrl(path: string): string {
  const base = (typeof window !== 'undefined' && window.NORTHLIGHT_API_BASE) || '';
  return base.replace(/\/+$/, '') + path;
}

/** Points HttpClient's relative `/api` requests at the configured backend. */
export const apiBaseInterceptor: HttpInterceptorFn = (request, next) =>
  request.url.startsWith('/api/') ? next(request.clone({ url: apiUrl(request.url) })) : next(request);
