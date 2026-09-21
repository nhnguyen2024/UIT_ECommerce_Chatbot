import { ChangeDetectionStrategy, Component, OnInit, computed, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ChatService, Citation, Product, StreamEvent } from './chat.service';
import { OrderMapComponent, Tracking } from './order-map.component';
import { iconFor } from '../shared/product-icons';

/** A tool call, as the shopper sees it. */
interface ToolStep {
  name: string;
  label: string;
  /** Shown once the step finishes, so a completed lookup stops saying "Đang…". */
  doneLabel: string;
  done: boolean;
  ok: boolean;
}

interface Message {
  role: 'user' | 'assistant';
  text: string;
  citations?: Citation[];
  products?: Product[];
  tracking?: Tracking[];
  tools?: ToolStep[];
  pending?: boolean;
}

/**
 * Human-readable labels for the tools, in both languages.
 *
 * The raw tool name is an implementation detail. "search_policies" means
 * nothing to a shopper; "Reading store policies" tells them why they are
 * waiting, which is the entire point of showing progress at all.
 */
type Label = { vi: string; en: string };

const TOOL_LABELS: Record<string, { running: Label; done: Label }> = {
  search_products: {
    running: { vi: 'Đang tìm sản phẩm', en: 'Searching products' },
    done: { vi: 'Đã tìm sản phẩm', en: 'Searched products' },
  },
  get_product_details: {
    running: { vi: 'Đang xem chi tiết sản phẩm', en: 'Reading product details' },
    done: { vi: 'Đã xem chi tiết sản phẩm', en: 'Read product details' },
  },
  compare_products: {
    running: { vi: 'Đang so sánh sản phẩm', en: 'Comparing products' },
    done: { vi: 'Đã so sánh sản phẩm', en: 'Compared products' },
  },
  search_policies: {
    running: { vi: 'Đang tra cứu chính sách', en: 'Reading store policies' },
    done: { vi: 'Đã tra cứu chính sách', en: 'Read store policies' },
  },
  get_order_status: {
    running: { vi: 'Đang kiểm tra đơn hàng', en: 'Checking your order' },
    done: { vi: 'Đã kiểm tra đơn hàng', en: 'Checked your order' },
  },
  create_handoff: {
    running: { vi: 'Đang chuyển cho nhân viên', en: 'Connecting you to an agent' },
    done: { vi: 'Đã chuyển cho nhân viên', en: 'Passed to an agent' },
  },
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
  imports: [FormsModule, OrderMapComponent],
  templateUrl: './chat.component.html',
  styleUrl: './chat.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ChatComponent implements OnInit {
  private readonly chat = inject(ChatService);

  readonly sessionId = signal<string | null>(localStorage.getItem('northlight-session'));
  readonly messages = signal<Message[]>([]);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly lang = signal<'vi' | 'en'>('vi');
  draft = '';

  /**
   * A question handed over from another page (?ask=...), such as "track this
   * order" from the confirmation page. It fills the box rather than sending,
   * so the shopper sees what will be asked and can edit it.
   */
  readonly handedQuestion = input<string | undefined>(undefined, { alias: 'ask' });

  ngOnInit(): void {
    const handed = this.handedQuestion();
    if (handed) this.draft = handed.slice(0, 4000);
  }

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
                tracking: message.tracking,
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
            tools: [...(last.tools ?? []), { name, ...this.toolLabels(name), done: false, ok: true }],
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

      case 'tracking':
        this.patchLast((last) => ({ ...last, tracking: event.items as Tracking[] }));
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

  private toolLabels(name: string): { label: string; doneLabel: string } {
    const entry = TOOL_LABELS[name];
    if (!entry) {
      const plain = name.replace(/_/g, ' ');
      return { label: plain, doneLabel: plain };
    }
    const lang = this.lang();
    return { label: entry.running[lang], doneLabel: entry.done[lang] };
  }

  /** Which tools ran, for the collapsed trace under a finished reply. */
  toolSummary(tools: ToolStep[] | undefined): string {
    if (!tools?.length) return '';
    return [...new Set(tools.map((step) => step.name.replace(/_/g, ' ')))].join(' · ');
  }

  /**
   * The reply without its [ref:...] markers.
   *
   * The markers are for the backend's citation check; the sources already show
   * as chips under the reply. A marker still being streamed ("[ref:retu") is cut
   * too, so it never flashes on screen before it is complete.
   */
  displayText(text: string): string {
    return text
      .replace(/[ \t]*\[ref:[^\]\s]+\]/g, '')
      .replace(/[ \t]*\[(r(e(f(:[^\]\s]*)?)?)?)?$/, '');
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
    return iconFor(product.sku, product.category);
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
