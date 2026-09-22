import { Routes } from '@angular/router';
import { adminGuard } from './admin/auth.service';

/**
 * Every page is lazily loaded. The dashboard pulls in chart geometry and table
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
    path: 'admin/login',
    loadComponent: () => import('./admin/login.component').then((m) => m.LoginComponent),
    title: 'Northlight · Staff sign-in',
  },
  {
    path: 'admin',
    canActivate: [adminGuard],
    loadComponent: () => import('./admin/admin.component').then((m) => m.AdminComponent),
    title: 'Northlight Support · Operations',
  },
  {
    path: 'admin/insights',
    canActivate: [adminGuard],
    loadComponent: () => import('./admin/insights.component').then((m) => m.InsightsComponent),
    title: 'Northlight · Insights',
  },
  {
    path: 'shop',
    loadComponent: () => import('./shop/catalog.component').then((m) => m.CatalogComponent),
    title: 'Northlight · Shop',
  },
  {
    path: 'shop/:sku',
    loadComponent: () => import('./shop/product.component').then((m) => m.ProductComponent),
    title: 'Northlight · Product',
  },
  {
    path: 'cart',
    loadComponent: () => import('./shop/cart.component').then((m) => m.CartComponent),
    title: 'Northlight · Cart',
  },
  {
    path: 'checkout',
    loadComponent: () => import('./shop/checkout.component').then((m) => m.CheckoutComponent),
    title: 'Northlight · Checkout',
  },
  {
    path: 'order/:code',
    loadComponent: () => import('./shop/placed.component').then((m) => m.PlacedComponent),
    title: 'Northlight · Order placed',
  },
  { path: '**', redirectTo: '' },
];
