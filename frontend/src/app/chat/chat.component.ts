import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ChatService, Citation, Product, StreamEvent } from './chat.service';

/** A tool call, as the shopper sees it. */
interface ToolStep {
  name: string;
  label: string;
  done: boolean;
  ok: boolean;
}

interface Message {
  role: 'user' | 'assistant';
  text: string;
  citations?: Citation[];
  products?: Product[];
  tools?: ToolStep[];
  pending?: boolean;
}

/**
 * One line-art glyph per catalogue category, drawn on a 24x24 grid.
 *
 * A shopper scanning three results reads the silhouette before the words, so a
 * phone has to look like a phone. The previous card showed the first two
 * letters of the brand instead, which distinguished nothing: half the
 * catalogue is Samsung or Sony, and two grey letters look identical whether
 * they sit on a television or a charging cable.
 *
 * Stroked rather than filled, so one path inherits `currentColor` and works on
 * either theme without a second asset.
 */
const CATEGORY_ICONS: Record<string, string> = {
  phones: 'M7.5 2.5h9a1.5 1.5 0 0 1 1.5 1.5v16a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 20V4a1.5 1.5 0 0 1 1.5-1.5Z M10.5 18.6h3',
  tablets: 'M5.5 2.5h13a1.5 1.5 0 0 1 1.5 1.5v16a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 20V4a1.5 1.5 0 0 1 1.5-1.5Z M10 18.6h4',
  laptops: 'M5 5.5h14v10H5z M2.5 18.5h19 M10 15.5h4',
  audio: 'M4.5 14.5v-2.5a7.5 7.5 0 0 1 15 0v2.5 M4.5 13.5h2.2v6H6a1.5 1.5 0 0 1-1.5-1.5Z M19.5 13.5h-2.2v6H18a1.5 1.5 0 0 0 1.5-1.5Z',
  wearables: 'M9 7.5h6A1.5 1.5 0 0 1 16.5 9v6a1.5 1.5 0 0 1-1.5 1.5H9A1.5 1.5 0 0 1 7.5 15V9A1.5 1.5 0 0 1 9 7.5Z M9.5 7.5v-4h5v4 M9.5 16.5v4h5v-4 M16.5 10.8h1.4v2.4h-1.4',
  televisions: 'M3 4.5h18v12H3z M8.5 20.5h7 M12 16.5v4',
  'home-appliances': 'M5.5 2.5h13a1 1 0 0 1 1 1v17a1 1 0 0 1-1 1h-13a1 1 0 0 1-1-1v-17a1 1 0 0 1 1-1Z M4.5 7.5h15 M12 10.5a3.8 3.8 0 1 0 0 7.6 3.8 3.8 0 0 0 0-7.6Z M16.5 5h1',
  accessories: 'M9 2.5v5.5 M15 2.5v5.5 M6.8 8h10.4v3a5.2 5.2 0 0 1-10.4 0z M12 16.2v5.3',
};

/**
 * SKU prefix to category, for the rare card that arrives without a category.
 * `get_product_details` returns a document the model chose by SKU, and older
 * stored conversations predate the field entirely.
 */
const SKU_CATEGORIES: Record<string, string> = {
  PHN: 'phones',
  TAB: 'tablets',
  LAP: 'laptops',
  AUD: 'audio',
  WAT: 'wearables',
  TVS: 'televisions',
  KIT: 'home-appliances',
  ACC: 'accessories',
};

/**
 * Human-readable labels for the tools, in both languages.
 *
 * The raw tool name is an implementation detail. "search_policies" means
 * nothing to a shopper; "Reading store policies" tells them why they are
 * waiting, which is the entire point of showing progress at all.
 */
const TOOL_LABELS: Record<string, { vi: string; en: string }> = {
  search_products: { vi: 'Đang tìm sản phẩm', en: 'Searching products' },
  get_product_details: { vi: 'Đang xem chi tiết sản phẩm', en: 'Reading product details' },
  compare_products: { vi: 'Đang so sánh sản phẩm', en: 'Comparing products' },
  search_policies: { vi: 'Đang tra cứu chính sách', en: 'Reading store policies' },
  get_order_status: { vi: 'Đang kiểm tra đơn hàng', en: 'Checking your order' },
  create_handoff: { vi: 'Đang chuyển cho nhân viên', en: 'Connecting you to an agent' },
};

const SUGGESTIONS = [
  { vi: 'Tai nghe chống ồn dưới 2 triệu?', en: 'Noise cancelling headphones under 2 million?' },
  { vi: 'Chính sách đổi trả trong bao lâu?', en: 'How long is the return window?' },
  { vi: 'Đơn DH2026090001, sđt 0901234567', en: 'Order DH2026090001, phone 0901234567' },
  { vi: 'Laptop cho sinh viên IT tầm 20 triệu', en: 'A laptop for a CS student, around 20 million' },
];

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './chat.component.html',
  styleUrl: './chat.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ChatComponent {
  private readonly chat = inject(ChatService);

  readonly sessionId = signal<string | null>(localStorage.getItem('northlight-session'));
  readonly messages = signal<Message[]>([]);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly lang = signal<'vi' | 'en'>('vi');
  draft = '';

  /** Starter prompts only make sense before the conversation begins. */
  readonly showSuggestions = computed(() => this.messages().length === 0);
  readonly suggestions = SUGGESTIONS;

  constructor() {
    const saved = this.sessionId();
    if (saved) {
      this.chat.getConversation(saved).subscribe({
        next: (conversation) => {
          if (conversation.messages.length) {
            this.messages.set(
              conversation.messages.map((message) => ({
                role: message.role,
                text: message.text,
                citations: message.citations,
                products: message.products,
              })),
            );
          }
        },
        error: () => {
          // The session is gone, or the datastore is unreachable. Either way,
          // start clean rather than showing an error for an empty screen.
          localStorage.removeItem('northlight-session');
          this.sessionId.set(null);
        },
      });
    }
  }

  ask(text: string): void {
    this.draft = text;
    this.send();
  }

  send(): void {
    const message = this.draft.trim();
    if (!message || this.busy()) return;

    this.draft = '';
    this.error.set('');
    this.busy.set(true);
    this.messages.update((items) => [
      ...items,
      { role: 'user', text: message },
      { role: 'assistant', text: '', pending: true, tools: [] },
    ]);

    this.chat.stream(message, this.sessionId()).subscribe({
      next: (event) => this.handleEvent(event),
      error: () => {
        this.busy.set(false);
        this.error.set('The assistant is unreachable right now. Please try again.');
        this.messages.update((items) => items.filter((item) => !item.pending));
      },
      complete: () => this.busy.set(false),
    });
  }

  handleKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.send();
    }
  }

  reset(): void {
    localStorage.removeItem('northlight-session');
    this.sessionId.set(null);
    this.messages.set([]);
    this.error.set('');
  }

  private handleEvent(event: StreamEvent): void {
    switch (event.type) {
      case 'session':
        if (event.session_id) {
          this.sessionId.set(event.session_id);
          localStorage.setItem('northlight-session', event.session_id);
        }
        break;

      case 'start':
        if (event.lang) this.lang.set(event.lang);
        break;

      case 'text':
        if (event.delta) {
          this.patchLast((last) => ({ ...last, text: last.text + event.delta }));
        }
        break;

      case 'tool_start':
        if (event.name) {
          const name = event.name;
          this.patchLast((last) => ({
            ...last,
            tools: [...(last.tools ?? []), { name, label: this.toolLabel(name), done: false, ok: true }],
          }));
        }
        break;

      case 'tool_end':
        if (event.name) {
          const name = event.name;
          const ok = event.ok !== false;
          this.patchLast((last) => ({
            ...last,
            // Mark the first still-running step with this name. A turn can call
            // the same tool twice, and completing the wrong one would leave a
            // spinner running forever.
            tools: markFirstPending(last.tools ?? [], name, ok),
          }));
        }
        break;

      case 'products':
        this.patchLast((last) => ({ ...last, products: event.items as Product[] }));
        break;

      case 'citations':
        this.patchLast((last) => ({ ...last, citations: event.items as Citation[] }));
        break;

      case 'error':
        this.error.set(event.message ?? 'The assistant could not complete that request.');
        break;

      case 'done':
        this.patchLast((last) => ({
          ...last,
          text: event.text ?? last.text,
          pending: false,
          // Any tool still marked running never reported an end frame; the turn
          // is over, so stop showing it as in progress.
          tools: (last.tools ?? []).map((step) => ({ ...step, done: true })),
        }));
        break;
    }
  }

  private patchLast(update: (last: Message) => Message): void {
    this.messages.update((items) =>
      items.length ? [...items.slice(0, -1), update(items[items.length - 1])] : items,
    );
  }

  private toolLabel(name: string): string {
    const entry = TOOL_LABELS[name];
    if (!entry) return name.replace(/_/g, ' ');
    return this.lang() === 'vi' ? entry.vi : entry.en;
  }

  /** Which tools ran, for the collapsed trace under a finished reply. */
  toolSummary(tools: ToolStep[] | undefined): string {
    if (!tools?.length) return '';
    return [...new Set(tools.map((step) => step.name.replace(/_/g, ' ')))].join(' · ');
  }

  citationLabel(citation: Citation): string {
    const id = citation.source_id;
    if (citation.kind === 'policy') {
      const [doc, section] = id.split('#');
      return `${doc.replace(/-/g, ' ')} → ${(section ?? '').replace(/-/g, ' ')}`;
    }
    return id.replace(/^(product|order|ticket):/, '');
  }

  price(value: number | null | undefined): string {
    return value == null ? '' : new Intl.NumberFormat('vi-VN').format(value) + 'đ';
  }

  productName(product: Product): string {
    return product.name ?? product.name_vi ?? product.name_en ?? product.sku;
  }

  inStock(product: Product): boolean {
    return product.in_stock ?? (product.stock ?? 0) > 0;
  }

  /** The glyph for a product, falling back to its SKU prefix then to audio. */
  iconPath(product: Product): string {
    const slug = product.category ?? SKU_CATEGORIES[product.sku.slice(0, 3)];
    return CATEGORY_ICONS[slug ?? ''] ?? CATEGORY_ICONS['accessories'];
  }

  /**
   * Percentage off, rounded, or null when the product is not discounted.
   *
   * Shown as a badge because the saving is what decides the click on a
   * discounted item, and comparing two formatted đồng figures is slower than
   * reading one number.
   */
  discount(product: Product): number | null {
    const { price, sale_price: sale } = product;
    if (price == null || sale == null || sale >= price) return null;
    return Math.round(((price - sale) / price) * 100);
  }
}

/**
 * Complete the earliest step matching `name` that is still running.
 *
 * Kept as a free function so the "same tool called twice" case is easy to see
 * and to test: completing every match at once would clear a second call that
 * has not finished.
 */
function markFirstPending(tools: ToolStep[], name: string, ok: boolean): ToolStep[] {
  const index = tools.findIndex((step) => step.name === name && !step.done);
  if (index === -1) return tools;
  const next = [...tools];
  next[index] = { ...next[index], done: true, ok };
  return next;
}
