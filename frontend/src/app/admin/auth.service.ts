import { Injectable, computed, inject, signal } from '@angular/core';
import { HttpClient, HttpInterceptorFn, HttpErrorResponse } from '@angular/common/http';
import { CanActivateFn, Router } from '@angular/router';
import { Observable, catchError, tap, throwError } from 'rxjs';

interface Session {
  token: string;
  expires_at: number; // seconds since epoch
}

const KEY = 'northlight.admin.session';

/**
 * Staff session for the Operations and Insights pages.
 *
 * The token lives in sessionStorage, so it ends when the tab closes and is
 * never shared with other tabs or kept on a shared computer. The backend
 * decides what the token is worth; this service only carries it.
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly session = signal<Session | null>(read());

  readonly signedIn = computed(() => {
    const s = this.session();
    return !!s && s.expires_at * 1000 > Date.now();
  });

  token(): string | null {
    return this.signedIn() ? this.session()!.token : null;
  }

  login(password: string): Observable<Session> {
    return this.http.post<Session>('/api/admin/login', { password }).pipe(
      tap((s) => {
        try {
          sessionStorage.setItem(KEY, JSON.stringify(s));
        } catch {
          /* private mode: the session simply lasts for this page only */
        }
        this.session.set(s);
      }),
    );
  }

  logout(): void {
    try {
      sessionStorage.removeItem(KEY);
    } catch {
      /* nothing stored */
    }
    this.session.set(null);
  }
}

function read(): Session | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

/** Staff pages open only with a live session; otherwise go to sign-in and come back. */
export const adminGuard: CanActivateFn = (_route, state) => {
  const auth = inject(AuthService);
  return auth.signedIn() || inject(Router).createUrlTree(['/admin/login'], { queryParams: { next: state.url } });
};

/**
 * Adds the session token to /api/admin requests. A 401 means the session has
 * expired or the password changed: drop it and send the user to sign in again.
 */
export const adminAuthInterceptor: HttpInterceptorFn = (request, next) => {
  if (!request.url.includes('/api/admin/') || request.url.endsWith('/api/admin/login')) {
    return next(request);
  }
  const auth = inject(AuthService);
  const router = inject(Router);
  const token = auth.token();
  const authed = token ? request.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : request;
  return next(authed).pipe(
    catchError((error: unknown) => {
      if (error instanceof HttpErrorResponse && error.status === 401) {
        auth.logout();
        router.navigate(['/admin/login'], { queryParams: { next: router.url } });
      }
      return throwError(() => error);
    }),
  );
};
