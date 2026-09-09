import { Routes } from '@angular/router';

/**
 * Both pages are lazily loaded. The dashboard pulls in chart geometry and table
 * styling a shopper never sees, and the chat view is the one that has to appear
 * instantly, so neither should carry the other's weight in the initial bundle.
 */
export const routes: Routes = [
  {
    path: '',
    loadComponent: () => import('./chat/chat.component').then((m) => m.ChatComponent),
    title: 'Northlight Support',
  },
  {
    path: 'admin',
    loadComponent: () => import('./admin/admin.component').then((m) => m.AdminComponent),
    title: 'Northlight Support · Operations',
  },
  { path: '**', redirectTo: '' },
];
