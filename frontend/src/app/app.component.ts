import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

/** Application shell: a thin navigation bar over the routed page. */
@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    <nav class="nav">
      <a routerLink="/" routerLinkActive="on" [routerLinkActiveOptions]="{ exact: true }">Chat</a>
      <a routerLink="/admin" routerLinkActive="on">Operations</a>
    </nav>
    <router-outlet />
  `,
  styles: [`
    .nav {
      position: fixed; top: 0; right: 0; z-index: 20;
      display: flex; gap: 2px; padding: 10px 14px;
    }
    .nav a {
      padding: 6px 13px; text-decoration: none;
      font: 500 10px 'DM Mono', monospace; letter-spacing: .1em; text-transform: uppercase;
      color: #8e9289; background: rgba(244,239,231,.82); border: 1px solid transparent;
      backdrop-filter: blur(6px);
    }
    .nav a.on { color: #b47740; border-color: #ddd6cb; }
    @media (max-width: 700px) { .nav { padding: 6px 8px; } .nav a { padding: 5px 9px; } }
  `],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppComponent {}
