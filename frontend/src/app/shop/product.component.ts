import { ChangeDetectionStrategy, Component, effect, inject, input, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { iconFor } from '../shared/product-icons';
import { CartService, MAX_PER_LINE } from './cart.service';
import { CHANNEL_LABELS, Channel, ShopProduct, ShopService, discountPercent, formatPrice } from './shop.service';

/** One product: specifications, where it is listed, and a way into the assistant. */
@Component({
  selector: 'app-product',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './product.component.html',
  styleUrl: './shop.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ProductComponent {
  private readonly shop = inject(ShopService);
  private readonly router = inject(Router);
  readonly cart = inject(CartService);

  /** Bound from the route parameter. */
  readonly sku = input.required<string>();

  readonly product = signal<ShopProduct | null>(null);
  readonly missing = signal(false);
  readonly quantity = signal(1);
  readonly added = signal(false);
  readonly channels: Channel[] = ['website', 'shopee', 'lazada', 'tiktok_shop'];

  constructor() {
    effect(() => {
      const sku = this.sku();
      this.product.set(null);
      this.missing.set(false);
      this.shop.product(sku).subscribe({
        next: (product) => this.product.set(product),
        error: () => this.missing.set(true),
      });
    });
  }

  step(delta: number): void {
    const product = this.product();
    if (!product) return;
    this.quantity.set(Math.max(1, Math.min(this.quantity() + delta, MAX_PER_LINE, product.stock)));
  }

  addToCart(): void {
    const product = this.product();
    if (!product) return;
    this.cart.add(product, this.quantity());
    this.added.set(true);
    setTimeout(() => this.added.set(false), 1600);
  }

  /** Opens the assistant with a question about this product ready to send. */
  ask(): void {
    const product = this.product();
    if (!product) return;
    this.router.navigate(['/'], {
      queryParams: { ask: `Tư vấn giúp mình về ${product.name_vi} (${product.sku}). Máy này có ưu điểm gì?` },
    });
  }

  listed(channel: Channel): boolean {
    return this.product()?.listed_on.includes(channel) ?? false;
  }

  readonly label = (channel: Channel) => CHANNEL_LABELS[channel];
  readonly icon = (product: ShopProduct) => iconFor(product.sku, product.category);
  readonly price = formatPrice;
  readonly discount = discountPercent;
}
