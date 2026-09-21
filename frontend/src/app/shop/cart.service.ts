import { Injectable, computed, signal } from '@angular/core';
import { ShopProduct, effectivePrice } from './shop.service';

export interface CartLine {
  sku: string;
  name: string;
  category: string;
  /** Price when added, for display only. The server prices the order. */
  unit_price: number;
  quantity: number;
  stock: number;
}

const STORAGE_KEY = 'northlight-cart';
export const MAX_PER_LINE = 10;

/**
 * The cart, kept in the browser.
 *
 * Guest checkout has no account to attach a server-side cart to, and a cart is
 * a per-visitor convenience: losing it in a private window costs a re-add, not
 * an order. Storage access is wrapped because it can throw or be unavailable.
 */
@Injectable({ providedIn: 'root' })
export class CartService {
  readonly lines = signal<CartLine[]>(this.load());
  readonly count = computed(() => this.lines().reduce((sum, line) => sum + line.quantity, 0));
  readonly subtotal = computed(() => this.lines().reduce((sum, line) => sum + line.unit_price * line.quantity, 0));

  add(product: ShopProduct, quantity = 1): void {
    const limit = Math.min(MAX_PER_LINE, product.stock);
    this.update((lines) => {
      const existing = lines.find((line) => line.sku === product.sku);
      if (existing) {
        return lines.map((line) =>
          line.sku === product.sku ? { ...line, quantity: Math.min(line.quantity + quantity, limit) } : line,
        );
      }
      return [
        ...lines,
        {
          sku: product.sku,
          name: product.name_vi,
          category: product.category,
          unit_price: effectivePrice(product),
          quantity: Math.min(quantity, limit),
          stock: product.stock,
        },
      ];
    });
  }

  setQuantity(sku: string, quantity: number): void {
    this.update((lines) =>
      lines.map((line) =>
        line.sku === sku ? { ...line, quantity: Math.max(1, Math.min(quantity, MAX_PER_LINE, line.stock)) } : line,
      ),
    );
  }

  remove(sku: string): void {
    this.update((lines) => lines.filter((line) => line.sku !== sku));
  }

  clear(): void {
    this.update(() => []);
  }

  private update(change: (lines: CartLine[]) => CartLine[]): void {
    this.lines.update(change);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this.lines()));
    } catch {
      // Storage unavailable: the cart still works for this visit.
    }
  }

  private load(): CartLine[] {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]');
      return Array.isArray(saved) ? saved : [];
    } catch {
      return [];
    }
  }
}
