import { ChangeDetectionStrategy, Component, inject, input } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { PlacedOrder, formatPrice } from './shop.service';

/** Order confirmation, and the hand-off to the assistant that tracks it. */
@Component({
  selector: 'app-placed',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './placed.component.html',
  styleUrl: './shop.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PlacedComponent {
  private readonly router = inject(Router);

  /** Bound from the route parameter. */
  readonly code = input.required<string>();

  // Present when arriving from checkout; absent on a reload, where only the
  // code (in the URL) survives. The page degrades to the code alone.
  private readonly state = this.router.getCurrentNavigation()?.extras.state as
    | { order?: PlacedOrder; phone?: string }
    | undefined;
  readonly order = this.state?.order ?? null;
  private readonly phone = this.state?.phone ?? '';

  track(): void {
    const contact = this.phone ? `, số điện thoại ${this.phone}` : '';
    this.router.navigate(['/'], {
      queryParams: { ask: `Đơn ${this.code()}${contact} của mình tới đâu rồi?` },
    });
  }

  readonly price = formatPrice;
}
