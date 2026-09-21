import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { iconFor } from '../shared/product-icons';
import { CartService, MAX_PER_LINE } from './cart.service';
import { formatPrice } from './shop.service';

@Component({
  selector: 'app-cart',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './cart.component.html',
  styleUrl: './shop.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CartComponent {
  readonly cart = inject(CartService);
  readonly max = MAX_PER_LINE;
  readonly price = formatPrice;
  readonly icon = (sku: string, category: string) => iconFor(sku, category);
}
