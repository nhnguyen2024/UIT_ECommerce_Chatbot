import { ChangeDetectionStrategy, Component, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { HttpErrorResponse } from '@angular/common/http';
import { AuthService } from './auth.service';

/** Sign-in for staff pages. Shoppers never need it: the shop and the assistant stay public. */
@Component({
  selector: 'app-admin-login',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './login.component.html',
  styleUrls: ['../shop/shop.css', './login.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LoginComponent {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  /** Where to return after signing in (query param `next`). */
  readonly next = input<string>('/admin');

  password = '';
  readonly busy = signal(false);
  readonly error = signal('');

  submit(): void {
    if (!this.password || this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    this.auth.login(this.password).subscribe({
      next: () => {
        const target = this.next()?.startsWith('/admin') ? this.next() : '/admin';
        this.router.navigateByUrl(target);
      },
      error: (e: unknown) => {
        this.busy.set(false);
        const status = e instanceof HttpErrorResponse ? e.status : 0;
        this.error.set(
          status === 401 ? 'Wrong password.' : status === 503 ? 'Sign-in is not configured on the server.' : 'Could not reach the server. Try again.',
        );
      },
    });
  }
}
