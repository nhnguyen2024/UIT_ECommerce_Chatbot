import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { iconFor } from '../shared/product-icons';
import { CartService } from './cart.service';
import {
  CHANNEL_LABELS,
  Category,
  Channel,
  ShopProduct,
  ShopService,
  Sort,
  discountPercent,
  formatPrice,
} from './shop.service';

/**
 * The storefront: one catalogue, and where else each product is sold.
 *
 * The channel badges are the point of this page. The same product is listed on
 * the website and on marketplaces, which is why the support assistant has to
 * answer for orders from all of them.
 */
@Component({
  selector: 'app-catalog',
  standalone: true,
  imports: [FormsModule, RouterLink],
  templateUrl: './catalog.component.html',
  styleUrl: './shop.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CatalogComponent {
  private readonly shop = inject(ShopService);
  readonly cart = inject(CartService);

  readonly categories = signal<Category[]>([]);
  readonly products = signal<ShopProduct[]>([]);
  readonly total = signal(0);
  readonly loading = signal(false);
  readonly error = signal('');
  readonly category = signal<string | null>(null);
  readonly added = signal<string | null>(null);
  query = '';
  sort: Sort = 'popular';
  private page = 1;

  readonly sorts: { value: Sort; label: string }[] = [
    { value: 'popular', label: 'Most reviewed' },
    { value: 'rating', label: 'Top rated' },
    { value: 'price_asc', label: 'Price: low to high' },
    { value: 'price_desc', label: 'Price: high to low' },
  ];

  constructor() {
    this.shop.categories().subscribe({ next: (rows) => this.categories.set(rows) });
    this.load(true);
  }

  pick(slug: string | null): void {
    this.category.set(slug);
    this.load(true);
  }

  search(): void {
    this.load(true);
  }

  more(): void {
    this.page += 1;
    this.load(false);
  }

  addToCart(product: ShopProduct): void {
    this.cart.add(product);
    this.added.set(product.sku);
    setTimeout(() => {
      if (this.added() === product.sku) this.added.set(null);
    }, 1400);
  }

  private load(reset: boolean): void {
    if (reset) this.page = 1;
    this.loading.set(true);
    this.error.set('');
    this.shop
      .products({ category: this.category() ?? undefined, q: this.query.trim() || undefined, sort: this.sort, page: this.page })
      .subscribe({
        next: (page) => {
          this.products.set(reset ? page.items : [...this.products(), ...page.items]);
          this.total.set(page.total);
          this.loading.set(false);
        },
        error: () => {
          this.error.set('The catalogue could not be loaded. Is the backend running?');
          this.loading.set(false);
        },
      });
  }

  readonly icon = (product: ShopProduct) => iconFor(product.sku, product.category);
  readonly price = formatPrice;
  readonly discount = discountPercent;
  readonly marketplaces = (product: ShopProduct): string[] =>
    product.listed_on.filter((channel) => channel !== 'website').map((channel: Channel) => CHANNEL_LABELS[channel]);
}
