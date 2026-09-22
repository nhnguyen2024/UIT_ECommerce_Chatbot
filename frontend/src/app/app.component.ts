import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { AuthService } from './admin/auth.service';
import { CartService } from './shop/cart.service';
import { ThemeService } from './theme.service';

/** Application shell: the aurora band, navigation, and the routed page. */
@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.component.html',
  styleUrl: './app.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppComponent {
  private readonly themes = inject(ThemeService);
  readonly theme = this.themes.theme;
  readonly cart = inject(CartService);
  readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  signOut(): void {
    this.auth.logout();
    this.router.navigateByUrl('/shop');
  }

  toggleTheme(): void {
    this.themes.toggle();
  }
}
