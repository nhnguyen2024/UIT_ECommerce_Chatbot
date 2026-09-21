import { HttpErrorResponse } from '@angular/common/http';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { CartService } from './cart.service';
import { Province, ShopService, formatPrice } from './shop.service';

/**
 * Guest checkout. No account and no real payment: the order is an ordinary
 * website order, which the assistant can then track like any other.
 */
@Component({
  selector: 'app-checkout',
  standalone: true,
  imports: [FormsModule, RouterLink],
  templateUrl: './checkout.component.html',
  styleUrl: './shop.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CheckoutComponent {
  private readonly shop = inject(ShopService);
  private readonly router = inject(Router);
  readonly cart = inject(CartService);

  readonly provinces = signal<Province[]>([]);
  readonly fee = signal<number | null>(null);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly total = computed(() => this.cart.subtotal() + (this.fee() ?? 0));

  name = '';
  phone = '';
  email = '';
  province = '';
  payment: 'cod' | 'bank_transfer' = 'cod';

  constructor() {
    this.shop.provinces().subscribe({ next: (rows) => this.provinces.set(rows) });
  }

  quote(): void {
    if (!this.province) {
      this.fee.set(null);
      return;
    }
    this.shop.shipping(this.province, this.cart.subtotal()).subscribe({
      next: (quote) => this.fee.set(quote.shipping_fee),
    });
  }

  place(): void {
    if (this.busy() || !this.cart.lines().length) return;
    this.busy.set(true);
    this.error.set('');
    const phone = this.phone;
    this.shop
      .placeOrder({
        items: this.cart.lines().map((line) => ({ sku: line.sku, quantity: line.quantity })),
        name: this.name,
        phone: this.phone,
        email: this.email,
        province: this.province,
        payment_method: this.payment,
      })
      .subscribe({
        next: (order) => {
          this.cart.clear();
          // The phone number travels in navigation state only, to prefill the
          // assistant on the next page. It is never stored in the browser.
          this.router.navigate(['/order', order.order_code], { state: { order, phone } });
        },
        error: (failure: HttpErrorResponse) => {
          this.busy.set(false);
          this.error.set(describe(failure));
        },
      });
  }

  readonly price = formatPrice;
}

function describe(failure: HttpErrorResponse): string {
  const detail = failure.error?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && detail.length) {
    // FastAPI validation errors: "Value error, Enter a valid email address".
    return detail.map((item: { msg?: string }) => (item.msg ?? '').replace(/^Value error, /, '')).join(' ');
  }
  return 'The order could not be placed. Please try again.';
}
